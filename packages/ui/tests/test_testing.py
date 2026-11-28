"""moxi_ui.testing: the plugin's load, the test shell over the fixture module, and the
contract and width checks in a browser."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest
from moxi_dev.testing import short_dir, wait_for
from moxi_ui.session import LIMIT
from moxi_ui.testing import WAIT_S
from moxi_ui.testing.shell import Session
from playwright.sync_api import expect

import fixture_kit

#: The bound on a test that drives a page in this process, kept by a thread that ends the
#: test's process past it. A test whose browser work runs in an inner pytest run is
#: bounded by that run's own timeout instead, which kills the inner run and fails the test.
IN_PAGE = pytest.mark.timeout(120, method="thread")


def test_loading_the_plugin_imports_no_moxi_ui_submodule_and_no_playwright(tmp_path):
    (tmp_path / "test_plain.py").write_text(textwrap.dedent("""
        import sys

        def test_plain(request):
            assert request.config.pluginmanager.has_plugin("moxi_ui")
            loaded = sorted(m for m in sys.modules if m.split(".")[0] in ("moxi_ui", "moxi",
                            "playwright"))
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
    browsers = _browsers_dir(ui_browser.browser_type.executable_path)
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
    assert {c["name"] for c in modules["fixture"]["components"]} == {
        "page", "block", "row-one", "row-two", "tag"}
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


def test_the_shells_reads_reuse_one_sign_in_so_a_browsers_session_outlives_them(ui_shell):
    # A sign-in of its own stands in for a browser's, made after the shell's first.
    browser = Session.sign_in(ui_shell.port, ui_shell.ticket())
    first = ui_shell.session()
    for _ in range(LIMIT + 5):
        again = ui_shell.session(timeout=10)
        assert again.get("/m/ui/api/modules")[0] == 200
        assert again.timeout == 10
    assert (again.cookie, again.key) == (first.cookie, first.key)
    assert browser.get("/m/ui/api/modules")[0] == 200


def test_the_shells_session_signs_in_again_once_the_shell_drops_it(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site])
    kept = shell.session()
    for _ in range(LIMIT):
        Session.sign_in(shell.port, shell.ticket())
    assert kept.get("/m/ui/api/modules")[0] != 200
    again = shell.session()
    assert again.cookie != kept.cookie
    assert again.get("/m/ui/api/modules")[0] == 200


def test_a_fixture_that_fails_its_start_is_a_502_naming_exit_3(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site], child_args=["--idle-s", "0.5"])
    fixture_kit.fail_start(shell)
    status, body = shell.session().get("/m/fixture/api/summary")
    assert status == 502 and body["error"] == "start" and body["exit"] == 3
    assert shell.wait_server("fixture", "failed")["failed_in_row"] == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="reads a page server's environment from /proc")
def test_a_shells_env_reaches_its_page_servers(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site], env={"GIT_CONFIG_NOSYSTEM": "1"})
    assert shell.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert shell.session().get("/m/fixture/api/summary")[0] == 200
    pid = shell.wait_server("fixture", "running")["pid"]
    proc_env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    assert b"GIT_CONFIG_NOSYSTEM=1" in proc_env
    assert f"HOME={shell.home}".encode() in proc_env


def test_a_shell_reads_the_record_under_the_test_kits_anchor(fence, tmp_path, monkeypatch):
    from moxi_ui.testing.shell import shell_env

    assert shell_env(tmp_path)["MOXI_ANCHOR_HOME"] == str(fence.anchor_home)
    assert shell_env(tmp_path, gateway=True)["MOXI_ANCHOR_HOME"] == str(fence.anchor_home)
    # Outside the kit's fence it still keeps the record under its own home.
    monkeypatch.delenv("MOXI_ANCHOR_HOME")
    env = shell_env(tmp_path)
    assert env["MOXI_ANCHOR_HOME"] == env.get("LOCALAPPDATA", str(tmp_path))


def test_a_shell_and_what_it_starts_carry_the_test_kits_run_name(tmp_path, monkeypatch):
    from moxi.terminal import TEST_RUN_PID_ENV
    from moxi_ui.testing.shell import shell_env

    assert shell_env(tmp_path)[TEST_RUN_PID_ENV] == os.environ[TEST_RUN_PID_ENV]
    # As the shell starts its page servers and the `moxi` commands a save runs.
    done = subprocess.run([sys.executable, "-P", "-c",
                           "import moxi.terminal as t; print(t.agent())"],
                          env=shell_env(tmp_path), capture_output=True, text=True,
                          timeout=60)
    assert done.stdout.strip() == "None", done.stderr
    monkeypatch.delenv(TEST_RUN_PID_ENV)
    assert TEST_RUN_PID_ENV not in shell_env(tmp_path)


def test_a_shells_env_cant_move_it_out_of_its_home(tmp_path):
    from moxi_ui.testing.shell import TestShell, shell_env

    assert shell_env(tmp_path, extra={"PATH": "/opt/bin"})["PATH"] == "/opt/bin"
    for key in ("HOME", "MOXI_HOME", "XDG_STATE_HOME", "TMPDIR", "PYTHONPATH",
                "MOXI_ANCHOR_HOME"):
        with pytest.raises(ValueError, match=key):
            TestShell(tmp_path, env={key: str(tmp_path / "elsewhere")})
    with pytest.raises(ValueError, match="strings"):
        TestShell(tmp_path, env={"GIT_CONFIG_NOSYSTEM": 1})


def test_a_shell_given_a_home_keeps_its_files_there(ui_shells, fixture_site):
    with short_dir("th-") as home:
        shell = ui_shells(site=[fixture_site], home=home)
        try:
            assert shell.home == home
            assert shell.runtime_dir("fixture").is_relative_to(home)
            assert shell.session().get("/m/fixture/api/summary")[0] == 200
        finally:
            shell.stop()


def test_a_left_out_module_is_not_in_the_snapshot(ui_shells, fixture_site):
    shell = ui_shells(site=[fixture_site], leave_out=["cache"])
    status, view = shell.session().get("/m/ui/api/modules")
    names = {m["name"] for m in view["modules"]}
    assert status == 200 and "fixture" in names and "cache" not in names
    assert shell.session().get("/m/cache/")[0] == 404


@pytest.mark.browser
@IN_PAGE
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
        ui_component_check(ui_page, "fixture", "block", tokens=("warn",), token_ms=1000)
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
             WAIT_S, interval=0.05, what=lambda: f"a push stream for {held}, not {without}: {last()}")


def _settings_backdrop(page):
    """Open the Settings page as the backdrop a component is placed beside, and wait until it
    has read every listed module: a read still out at the placement would count against the
    component."""
    page.evaluate("async () => (await import('/m/ui/client.js')).navigate('ui', 'settings')")
    view = page.locator("#moxi-panel > moxi-ui-page moxi-ui-settings")
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)


@pytest.mark.browser
@IN_PAGE
def test_the_contract_check_counts_against_a_component_only_the_push_topics_it_adds(
        ui_page, ui_component_check):
    # The push channel is one stream for the page, reopened with every topic the page
    # holds whenever a component subscribes. `elsewhere` stands for another module with a
    # component on the page: its topics are no topic of the fixture's. The settings panel
    # holds no fixture topic, so placing the fixture's block reopens the stream.
    _settings_backdrop(ui_page)
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
@IN_PAGE
def test_the_contract_check_says_when_it_could_not_read_the_held_topics(
        ui_page, ui_component_check, monkeypatch):
    # Without a page's recent requests (Playwright before 1.56), a topic the page held
    # before the placement counts against the component, and the failure says why.
    _settings_backdrop(ui_page)
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
@IN_PAGE
def test_the_width_check_names_what_reaches_past_the_edge(ui_page, ui_width_check):
    ui_page.evaluate("""() => {
      const wide = document.createElement("div");
      wide.id = "too-wide";
      wide.style.width = "500px";
      document.getElementById("moxi-panel").append(wide);
    }""")
    with pytest.raises(AssertionError, match="div#too-wide"):
        ui_width_check(ui_page)


#: Builders the width cases below put together in the shell's panel: a 900 px table, a div
#: with a style, and a host whose open shadow root holds what it is given.
WIDTH_KIT = """
const panel = document.getElementById("moxi-panel");
const table = () => {
  const made = document.createElement("table");
  made.id = "wide-table";
  made.style.width = "900px";
  const row = made.insertRow();
  row.insertCell().textContent = "a";
  row.insertCell().textContent = "b";
  return made;
};
const box = (id, style, ...children) => {
  const made = document.createElement("div");
  made.id = id;
  made.style.cssText = style;
  made.append(...children);
  return made;
};
const shadow = (...children) => {
  const host = document.createElement("div");
  host.attachShadow({mode: "open"}).append(...children);
  return host;
};
"""

#: Wide content a scroll container that ends inside the edge holds, so scrolling it
#: reaches the rest: the width check passes.
HELD = {
    "wrapper": 'panel.append(box("wrapper", "overflow-x: auto", table()));',
    "across-a-shadow-root": 'panel.append(box("wrapper", "overflow-x: auto", shadow(table())));',
    "slotted": """
      const host = shadow(box("wrapper", "overflow-x: auto", document.createElement("slot")));
      host.append(table());
      panel.append(host);""",
    "absolute-in-a-positioned-wrapper": """
      panel.append(box("wrapper", "overflow-x: auto; position: relative",
                       box("abs", "position: absolute; left: 0; width: 900px; height: 1px")));""",
    "hidden-box-it-fits-in": """
      panel.append(box("wrapper", "overflow-x: auto",
                       box("fits", "overflow: hidden; width: 900px", table())));""",
}

#: Wide content nothing scrolling holds, and what the check names for it.
NOT_HELD = {
    "hidden": ('panel.append(box("wrapper", "overflow: hidden", table()));',
               "table#wide-table"),
    "clip": ('panel.append(box("wrapper", "overflow-x: clip", table()));', "table#wide-table"),
    "hidden-inside-a-wrapper": (
        'panel.append(box("wrapper", "overflow-x: auto", box("cut", "overflow: hidden", table())));',
        "table#wide-table"),
    "wrapper-past-the-edge": (
        'panel.append(box("wrapper", "overflow-x: auto; width: 500px", table()));',
        "div#wrapper"),
    "absolute-past-its-wrapper": (
        """panel.append(box("wrapper", "overflow-x: auto",
                            box("abs", "position: absolute; left: 0; width: 900px; height: 1px")));""",
        "div#abs"),
    "fixed": (
        """panel.append(box("wrapper", "overflow-x: auto; position: relative",
                            box("fixed", "position: fixed; left: 0; width: 900px; height: 1px")));""",
        "div#fixed"),
}


@pytest.mark.browser
@IN_PAGE
@pytest.mark.parametrize("case", list(HELD))
def test_the_width_check_passes_what_a_scroll_container_inside_the_edge_holds(
        ui_page, ui_width_check, case):
    ui_page.set_viewport_size({"width": 360, "height": 800})
    ui_page.evaluate(f"() => {{ {WIDTH_KIT} {HELD[case]} }}")
    wrapper = ui_page.locator("#wrapper")
    # The wrapper does scroll: the table is wider than it.
    assert wrapper.evaluate("(node) => node.scrollWidth > node.clientWidth")
    ui_width_check(ui_page)


@pytest.mark.browser
@IN_PAGE
@pytest.mark.parametrize("case", list(NOT_HELD))
def test_the_width_check_names_what_no_scroll_container_inside_the_edge_holds(
        ui_page, ui_width_check, case):
    script, named = NOT_HELD[case]
    ui_page.evaluate(f"() => {{ {WIDTH_KIT} {script} }}")
    with pytest.raises(AssertionError, match=named):
        ui_width_check(ui_page)


@pytest.mark.browser
@IN_PAGE
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


def test_the_testing_extra_needs_a_platformdirs_that_reads_the_windows_overrides():
    """A gateway-mode test shell on Windows relies on `WIN_PD_OVERRIDE_*`, which only a
    recent platformdirs reads; with an older one its files would land in the real profile."""
    from importlib.metadata import requires

    wanted = [line for line in requires("moxi-ui") or [] if line.startswith("platformdirs")]
    assert wanted == ["platformdirs>=4.11.3; (sys_platform == 'win32') and extra == 'testing'"]


#: A browser's install directory, as Playwright names it: `chromium-1248`,
#: `chromium_headless_shell-1248`, `firefox-1555`, `webkit-2370`.
_INSTALL_DIR = re.compile(r"[a-z_]+-[0-9]+")


def _browsers_dir(executable: str | os.PathLike) -> Path:
    """The directory Playwright installs its browsers in, found from one browser's
    executable: the parent of the nearest ancestor named `<name>-<digits>`. On Linux that
    is three levels up; on macOS the executable sits deeper, inside an `.app` bundle."""
    for parent in Path(executable).parents:
        if _INSTALL_DIR.fullmatch(parent.name):
            return parent.parent
    raise AssertionError(f"no `<name>-<digits>` directory above {executable}")


@pytest.mark.parametrize("executable", [
    "/home/u/.cache/ms-playwright/chromium_headless_shell-1248/chrome-linux/headless_shell",
    "/home/u/.cache/ms-playwright/firefox-1555/firefox/firefox",
    "/Users/u/Library/Caches/ms-playwright/chromium_headless_shell-1248/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Users/u/Library/Caches/ms-playwright/webkit-2370/pw_run.sh",
])
def test_the_browsers_dir_is_found_from_a_linux_or_a_macos_executable(executable):
    assert _browsers_dir(executable) == Path(executable.split("/ms-playwright/")[0],
                                             "ms-playwright")


def test_a_path_with_no_install_dir_above_it_is_refused():
    with pytest.raises(AssertionError, match="no `<name>-<digits>` directory"):
        _browsers_dir("/usr/bin/chromium")


@pytest.mark.browser
def test_a_failed_browser_test_keeps_its_trace_where_ui_traces_says(tmp_path, ui_browser):
    # The trace directory is outside the inner run's fence, as a repository directory is
    # in CI: the plugin makes it before any test is fenced, and the teardown that keeps
    # the trace makes nothing.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "conftest.py").write_text('pytest_plugins = ["moxi_dev.testing"]\n')
    (run_dir / "test_fails.py").write_text(textwrap.dedent("""
        from types import SimpleNamespace

        def test_a_page_that_fails(ui_contexts):
            context = ui_contexts(SimpleNamespace(url="http://127.0.0.1:9/"), sign_in=False)
            context.new_page().set_content("<p>here</p>")
            assert False, "fails on purpose"
    """))
    traces_dir = tmp_path / "traces"
    browsers_dir = _browsers_dir(ui_browser.browser_type.executable_path)
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--ui-browser", ui_browser.browser_type.name,
                           "--ui-traces", str(traces_dir), str(run_dir)],
                          cwd=run_dir, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir)})
    assert done.returncode == 1, done.stdout + done.stderr
    summary = done.stdout.strip().splitlines()[-1]
    assert "1 failed" in summary and "error" not in summary, done.stdout
    assert [p.suffix for p in traces_dir.iterdir()] == [".zip"]


#: The inner run's bound on the fixtures' own calls, in seconds: `CALL_S`, shortened.
_INNER_CALL_S = 5
#: Seconds the inner run may take in all; past it the run is taken to hang.
_INNER_RUN_S = 90


@pytest.mark.browser
def test_a_teardown_whose_browser_stops_answering_ends_at_the_bound(tmp_path, ui_browser):
    # The inner test stops every process of its browser and fails, so its teardown keeps
    # the trace and closes the context: calls to a browser that never answers, which
    # Playwright gives no timeout. The plugin ends the browser's processes at the bound.
    import psutil

    stopped = tmp_path / "stopped"
    (tmp_path / "test_stops_answering.py").write_text(textwrap.dedent(f"""
        from types import SimpleNamespace

        import psutil

        import moxi_ui.testing

        moxi_ui.testing.CALL_S = {_INNER_CALL_S}

        def test_a_page_whose_browser_stops_answering(ui_contexts):
            context = ui_contexts(SimpleNamespace(url="http://127.0.0.1:9/"), sign_in=False)
            context.new_page().set_content("<p>here</p>")
            # This process's one child is Playwright's driver; every process under it is
            # the browser's.
            browser = [found for driver in psutil.Process().children()
                       for found in driver.children(recursive=True)]
            with open({str(stopped)!r}, "w") as fh:
                fh.write("\\n".join(f"{{p.pid}} {{p.create_time()}}" for p in browser))
            for found in browser:
                found.suspend()
            assert False, "fails on purpose"
    """))
    browsers_dir = _browsers_dir(ui_browser.browser_type.executable_path)
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--ui-browser", ui_browser.browser_type.name, str(tmp_path)],
                              cwd=tmp_path, capture_output=True, text=True,
                              timeout=_INNER_RUN_S,
                              env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir)})
        left = [found for found in _stopped(stopped) if found.is_running()
                and found.status() != psutil.STATUS_ZOMBIE]
    finally:
        for found in _stopped(stopped):
            try:
                found.kill()
            except psutil.Error:
                pass
    out = done.stdout + done.stderr
    assert stopped.read_text().split(), f"the inner test stopped no browser process:\n{out}"
    assert done.returncode == 1, out
    assert re.search(f"the browser stopped answering: (stopping a failed test's trace|closing a "
                     f"context) took more than {_INNER_CALL_S} s, so its processes were ended",
                     out), out
    took = re.search(r"1 failed, 1 error in ([\d.]+)s", done.stdout)
    assert took is not None and float(took.group(1)) >= _INNER_CALL_S, out
    assert left == [], f"the plugin left stopped processes running: {left}"


def test_a_call_that_returns_past_its_bound_leaves_one_warning_naming_it(monkeypatch):
    import time

    from moxi_ui import testing as kit

    monkeypatch.setattr(kit, "CALL_S", 0.2)
    driver = kit._Driver([])
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("error")
        assert driver.call("stopping Playwright", lambda: time.sleep(0.6) or "stopped") \
            == "stopped"
    assert driver.ended == "stopping Playwright"
    assert [(found.category, str(found.message)) for found in log] == [
        (kit.BoundWarning, "the browser stopped answering: stopping Playwright took more "
                           "than 0.2 s, so its processes were ended and the call went on")]


def test_a_call_within_its_bound_leaves_no_warning(monkeypatch):
    from moxi_ui import testing as kit

    monkeypatch.setattr(kit, "CALL_S", 5.0)
    driver = kit._Driver([])
    with warnings.catch_warnings(record=True) as log:
        warnings.simplefilter("always")
        assert driver.call("closing a context", lambda: "returned") == "returned"
    assert driver.ended is None and log == []


def test_a_teardown_call_past_its_bound_shows_in_the_runs_summary_and_fails_nothing(tmp_path):
    # A fake call that outlives a short bound, in a fixture's teardown, in a run under
    # xdist that turns every other warning into an error.
    (tmp_path / "test_outlives.py").write_text(textwrap.dedent("""
        import time

        import pytest

        import moxi_ui.testing as kit

        kit.CALL_S = 0.2

        @pytest.fixture
        def slow_stop():
            driver = kit._Driver([])
            yield
            driver.call("stopping Playwright", time.sleep, 0.6)

        def test_it(slow_stop):
            pass
    """))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-W", "error", "-n", "1", str(tmp_path)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert re.search(r"1 passed, 1 warning in", done.stdout), out
    assert ("BoundWarning: the browser stopped answering: stopping Playwright took more "
            "than 0.2 s, so its processes were ended and the call went on") in done.stdout, out


def _stopped(listed: Path) -> list:
    """The processes `listed` names (`<pid> <start time>` a line) that are still the ones
    it named."""
    import psutil

    if not listed.exists():
        return []
    found = []
    for entry in listed.read_text().split("\n"):
        if not entry:
            continue
        pid, made = entry.split()
        try:
            process = psutil.Process(int(pid))
            if process.create_time() == float(made):
                found.append(process)
        except psutil.Error:
            pass
    return found

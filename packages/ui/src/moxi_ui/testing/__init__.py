"""`moxi_ui.testing`: the pytest plugin a module's own tests use to run its pages in a real
shell and check its components against the component contract.

moxi-ui installs it as a `pytest11` plugin, so every pytest run in an environment that
holds moxi-ui loads it. Loading it imports only the standard library and pytest; the shell
and Playwright load only when a test uses one of its fixtures. The browser fixtures need
the `testing` extra (`moxi-ui[testing]`, which brings Playwright); a browser is installed
once with `playwright install chromium`.

A module whose `ui` is an optional extra uses these fixtures by name and never imports
`moxi_ui`. Every fixture is named `ui_*`:

`ui_shell` (module scope)
    A `TestShell`: a real shell in a process of its own, over this environment's installed
    modules, its files under a temporary home. Its options come from `ui_shell_options`.
`ui_shell_options` (module scope)
    The `ui_shell`'s options, `{}` by default. Override it in a test module or a
    `conftest.py` to give `site` (directories put on the shell's `PYTHONPATH`, holding
    distributions a test installs), `child_args` (for every page server the shell starts,
    such as `["--idle-s", "0.5"]`), `leave_out` (installed modules the shell's snapshot
    leaves out, such as `["cache"]`), `failed_for` (the seconds a page server's failed
    start answers with its failure, 10 unless given, for a test that waits out several)
    or `port` (the port it binds, 0 for one the system picks). No setting or variable
    reaches these.
`ui_shells` (module scope)
    `ui_shells(**options)` starts one more test shell with those options; each is stopped
    when the test module ends.
`ui_browser` (session scope)
    The Playwright browser, headless: Chromium, or the one `--ui-browser` names
    (`chromium`, `firefox` or `webkit`). One per pytest process, so under pytest-xdist
    each worker has its own. Tests that use no browser fixture, `asyncio.run()` among
    them, run beside browser tests in one run.
`ui_contexts`
    `ui_contexts(shell=None, *, sign_in=True, **context_args)` opens a fresh browser
    context on `shell` (`ui_shell` by default), its waits 5 s, signed in with its first
    page on the shell's document at `/m/ui/`. A shell is signed in once, through its
    sign-in page; each context after that starts from the cookie and key that sign-in
    stored, and a fresh sign-in replaces them once the shell refuses them. A trace is kept
    for a failed test, in `--ui-traces` (`<pytest's temp root>/ui-traces` by default).
    Each context closes when the test ends.
`ui_context`, `ui_page`
    One `ui_contexts()` context, and its first page, signed in on `ui_shell`.
`ui_component_check`
    `check(page, module, name, attrs=None, *, tokens=("accent",), allow=(), keep=False)`:
    the contract check on one component (see `moxi_ui.testing.browser.check_component`).
    The shell places it, it renders into an open shadow root, adds no style sheet to the
    document, takes each of `tokens` as a colour, and requests only under `/m/<module>/`.
`ui_width_check`
    `check(page, *, module=None, name="page", attrs=None, route="", width=360)`: the
    document has no horizontal scroll at 360 CSS px, with the module's panel open or its
    component placed (see `moxi_ui.testing.browser.check_width`).

A module's browser test, for example:

    import pytest

    pytestmark = pytest.mark.browser

    def test_the_chip_keeps_the_contract(ui_page, ui_component_check):
        ui_component_check(ui_page, "cache", "chip", {"session": "s1"}, tokens=("accent", "warn"))

    def test_the_page_fits_a_phone(ui_page, ui_width_check):
        ui_width_check(ui_page, module="cache")

Without a browser, `ui_shell.session()` is a signed-in session over plain HTTP, and
`ui_shell.servers()` and `ui_shell.kill(module)` reach the page servers the shell runs.
"""

from __future__ import annotations

import asyncio
import os
import re
import weakref
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

#: Milliseconds a browser action or wait may take before it fails.
WAIT_MS = 5000

_REPORTS = pytest.StashKey[dict[str, Any]]()
#: Each test shell's stored sign-in: the browser state (cookie and key) one sign-in left.
_SIGNED_IN: weakref.WeakKeyDictionary[Any, dict[str, Any]] = weakref.WeakKeyDictionary()
KEY_ITEM = "moxi-ui-key"
#: Playwright's event loop while `ui_browser` holds a browser. Between its calls,
#: Playwright's sync API marks that loop as this thread's running loop, and then
#: `asyncio.run()` refuses to start; so a test that uses no browser fixture runs with the
#: mark taken off, and a browser and asyncio tests can share one pytest run.
_playwright_loop: asyncio.AbstractEventLoop | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("moxi-ui", "moxi_ui.testing")
    group.addoption("--ui-browser", default="chromium", choices=("chromium", "firefox", "webkit"),
                    help="the browser moxi_ui.testing's browser fixtures run (default chromium)")
    group.addoption("--ui-traces", default=None, metavar="DIR",
                    help="where a failed browser test's Playwright trace goes (default "
                         "<pytest's temp root>/ui-traces)")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[Any]:
    report = yield
    item.stash.setdefault(_REPORTS, {})[report.when] = report
    return report


def _without_playwright_loop(item: pytest.Item) -> Iterator[None]:
    if _playwright_loop is None or "ui_browser" in item.fixturenames:
        return (yield)
    if asyncio._get_running_loop() is _playwright_loop:
        asyncio._set_running_loop(None)
    try:
        return (yield)
    finally:
        if _playwright_loop is not None:
            asyncio._set_running_loop(_playwright_loop)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    return (yield from _without_playwright_loop(item))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    return (yield from _without_playwright_loop(item))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    return (yield from _without_playwright_loop(item))


@pytest.fixture(scope="module")
def ui_shell_options() -> dict[str, Any]:
    """The `ui_shell`'s options: `site`, `child_args` and `leave_out` (see the module
    doc). Override it to give them."""
    return {}


@pytest.fixture(scope="module")
def ui_shells(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[..., Any]]:
    """`ui_shells(**options)`: one more test shell; each is stopped at the module's end."""
    from .shell import TestShell

    started: list[TestShell] = []

    def start(**options: Any) -> TestShell:
        shell = TestShell(tmp_path_factory.mktemp("ui-shell"), **options)
        started.append(shell)
        return shell.start()

    try:
        yield start
    finally:
        for shell in reversed(started):
            shell.stop()


@pytest.fixture(scope="module")
def ui_shell(ui_shells: Callable[..., Any], ui_shell_options: dict[str, Any]) -> Any:
    """A test shell over this environment's modules, with `ui_shell_options`."""
    return ui_shells(**ui_shell_options)


@pytest.fixture(scope="session")
def ui_browser(request: pytest.FixtureRequest) -> Iterator[Any]:
    """The Playwright browser, headless; one for the session. Firefox or WebKit that can't
    start on this machine skips the browser tests with the reason; Chromium that can't is
    an error."""
    from playwright.sync_api import Error, sync_playwright

    global _playwright_loop
    name = request.config.getoption("--ui-browser")
    playwright = sync_playwright().start()
    _playwright_loop = asyncio._get_running_loop()
    try:
        try:
            browser = getattr(playwright, name).launch()
        except Error as err:
            if name == "chromium":
                raise
            said = next((line.strip() for line in str(err).splitlines()
                         if "error" in line.lower() and "launch" not in line.lower()),
                        str(err).splitlines()[0])
            pytest.skip(f"{name} can't start on this machine: {said}")
        try:
            yield browser
        finally:
            # The last test's teardown may be one that runs with the loop's mark off.
            asyncio._set_running_loop(_playwright_loop)
            browser.close()
    finally:
        asyncio._set_running_loop(_playwright_loop)
        try:
            playwright.stop()
        finally:
            _playwright_loop = None
            asyncio._set_running_loop(None)


@pytest.fixture
def ui_contexts(ui_browser: Any, request: pytest.FixtureRequest,
                tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[..., Any]]:
    """`ui_contexts(shell=None, *, sign_in=True, **context_args)`: a fresh browser context
    on `shell` (`ui_shell` by default), signed in unless `sign_in` is false. Closed when
    the test ends, its trace kept when the test failed."""
    opened: list[Any] = []

    def open_context(shell: Any = None, *, sign_in: bool = True, **context_args: Any) -> Any:
        shell = shell if shell is not None else request.getfixturevalue("ui_shell")
        if sign_in:
            context_args.setdefault("storage_state", _stored_sign_in(ui_browser, shell))
        context = ui_browser.new_context(base_url=shell.url, **context_args)
        context.set_default_timeout(WAIT_MS)
        context.tracing.start(screenshots=True, snapshots=True)
        opened.append(context)
        if sign_in:
            _open_settled(context.new_page(), "/m/ui/")
        return context

    try:
        yield open_context
    finally:
        reports = request.node.stash.get(_REPORTS, {})
        failed = any(report.failed for report in reports.values())
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.nodeid).strip("-")
        for n, context in enumerate(opened):
            try:
                if failed:
                    given = request.config.getoption("--ui-traces")
                    where = Path(given) if given else tmp_path_factory.getbasetemp() / "ui-traces"
                    os.makedirs(where, exist_ok=True)
                    context.tracing.stop(path=str(where / f"{name}-{n}.zip"))
                else:
                    context.tracing.stop()
            finally:
                context.close()


def _open_settled(page: Any, path: str) -> None:
    """Open the shell's document at `path`, and wait until its modules view is placed and
    every request it began has finished, the push stream aside. A test that navigates
    at once would otherwise abort the home blocks' scripts mid-load, and the document
    would log that they could not load."""
    pending: set[Any] = set()

    def begun(request: Any) -> None:
        if "/m/ui/api/push" not in request.url:
            pending.add(request)

    def ended(request: Any) -> None:
        pending.discard(request)

    page.on("request", begun)
    page.on("requestfinished", ended)
    page.on("requestfailed", ended)
    try:
        page.goto(path)
        page.locator("#moxi-panel > moxi-ui-page").wait_for(state="attached")
        quiet = 0
        for _ in range(WAIT_MS // 25):
            # The sync API hands over request events only inside a call to it.
            page.wait_for_timeout(25)
            quiet = quiet + 1 if not pending else 0
            if quiet >= 2:
                break
    finally:
        page.remove_listener("request", begun)
        page.remove_listener("requestfinished", ended)
        page.remove_listener("requestfailed", ended)


def _stored_sign_in(browser: Any, shell: Any) -> dict[str, Any]:
    """The browser state `shell`'s one sign-in stored: signed in through the sign-in page
    the first time, and again whenever the shell no longer takes the stored cookie and
    key (a test signed out, or the session expired)."""
    from .shell import Session

    state = _SIGNED_IN.get(shell)
    if state is not None:
        cookie = "; ".join(f"{c['name']}={c['value']}" for c in state["cookies"])
        key = next((item["value"] for origin in state["origins"]
                    for item in origin["localStorage"] if item["name"] == KEY_ITEM), "")
        status, _ = Session(shell.port, cookie, key).get("/m/ui/api/modules")
        if status == 200:
            return state
    context = browser.new_context(base_url=shell.url)
    try:
        context.set_default_timeout(WAIT_MS)
        shell.sign_in(context.new_page())
        state = context.storage_state()
    finally:
        context.close()
    _SIGNED_IN[shell] = state
    return state


@pytest.fixture
def ui_context(ui_contexts: Callable[..., Any]) -> Any:
    """A fresh browser context signed in on `ui_shell`."""
    return ui_contexts()


@pytest.fixture
def ui_page(ui_context: Any) -> Any:
    """`ui_context`'s first page, on the shell's document at `/m/ui/`."""
    return ui_context.pages[0]


@pytest.fixture
def ui_component_check() -> Callable[..., Any]:
    """The contract check on one component (`browser.check_component`)."""
    from .browser import check_component

    return check_component


@pytest.fixture
def ui_width_check() -> Callable[..., Any]:
    """The 360 px width check (`browser.check_width`)."""
    from .browser import check_width

    return check_width

"""A browser test with turns' marks whose browser stops answering ends at its bound,
named in the run's report, rather than waiting on the browser with no end.

The test is a second pytest run, under xdist as a check runs it, whose one test takes
`MARKS`' timeout with a shorter time, opens a page, stops every process of its browser
and then calls the page. Its call and the teardown after it (a trace stopped, a context
closed) wait on a browser that never answers.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from panel_kit import MARKS

pytestmark = MARKS

#: The inner test's bound in seconds: `MARKS`' timeout, with this time.
INNER_S = 10
#: Seconds the inner run may take in all; past it this test fails with the run hung.
RUN_S = 90
#: The directory name Playwright gives each browser it installs.
_INSTALL_DIR = re.compile(r".+-\d+")

#: The inner run's test. `{pids}` gets each stopped process's id and start time.
CASE = '''
from types import SimpleNamespace

import psutil
import pytest

pytestmark = pytest.mark.timeout({bound_s}, **{kwargs!r})


def test_a_page_whose_browser_stops_answering(ui_contexts):
    context = ui_contexts(SimpleNamespace(url="http://127.0.0.1:9/"), sign_in=False)
    page = context.new_page()
    page.set_content("<p>here</p>")
    # This process's one child is Playwright's driver; every process under it is the
    # browser's.
    browser = [found for driver in psutil.Process().children()
               for found in driver.children(recursive=True)]
    with open({pids!r}, "w") as fh:
        fh.write("\\n".join(f"{{p.pid}} {{p.create_time()}}" for p in browser))
    for found in browser:
        found.suspend()
    page.evaluate("1")
'''


def _browsers_dir(executable: str) -> Path:
    """The directory Playwright installs its browsers in: the parent of the nearest
    ancestor of `executable` named `<name>-<digits>`."""
    for parent in Path(executable).parents:
        if _INSTALL_DIR.fullmatch(parent.name):
            return parent.parent
    raise AssertionError(f"no `<name>-<digits>` directory above {executable}")


def _end_stopped(pids: Path) -> None:
    """Kill each process `pids` lists that is still the one the inner test stopped."""
    if not pids.exists():
        return
    for entry in pids.read_text().split("\n"):
        if not entry:
            continue
        pid, made = entry.split()
        try:
            found = psutil.Process(int(pid))
            if found.create_time() == float(made):
                found.kill()
        except psutil.Error:
            pass


def test_a_browser_test_whose_browser_stops_answering_fails_at_its_bound(tmp_path, ui_browser):
    bound = next(mark for mark in MARKS if mark.name == "timeout")
    pids = tmp_path / "stopped"
    case = tmp_path / "test_stops_answering.py"
    case.write_text(CASE.format(bound_s=INNER_S, kwargs=bound.kwargs, pids=str(pids)))
    browsers_dir = _browsers_dir(ui_browser.browser_type.executable_path)
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "-n", "1", "--ui-browser", ui_browser.browser_type.name, str(case)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=RUN_S,
                              env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir)})
    finally:
        _end_stopped(pids)
    out = done.stdout + done.stderr
    assert pids.read_text().split(), f"the inner test stopped no browser process:\n{out}"
    assert done.returncode == 1, out
    assert ("crashed while running "
            "'test_stops_answering.py::test_a_page_whose_browser_stops_answering'") in out, out
    # The worker ended at the bound: the run took that long, and the worker's stacks it
    # printed stay with the worker.
    took = re.search(r"1 failed in ([\d.]+)s", done.stdout)
    assert took is not None and float(took.group(1)) >= INNER_S, out

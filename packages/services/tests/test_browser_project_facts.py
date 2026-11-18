"""services' `project-facts` in a browser, placed alone in a real shell whose services page
server acts on a fake supervisor (`browser_kit`): given projects and an opened one, it
reports each project's failed services and warnings and the opened one's `Services` row,
from services' own verdicts; it follows the `rows` topic without reading the route again;
a store services can't read reports null and the next good one puts the facts back; and
once removed it reports nothing more.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from browser_kit import fake_site, python, seed, written

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="services' page is checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: How long a test waits for the component to report, in seconds: the ui kit's bound.
WAIT_S = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_S
#: How long past the rows poller's period a test waits to see that nothing came.
QUIET_S = 7.0
#: When `preview` was stopped by hand: now, so the stop is after the last boot and holds.
STOPPED = datetime.now(timezone.utc).replace(microsecond=0)

#: The fake supervisor's units, by row name. `api`, which a test adds, has none: missing.
UNITS = {"written": {
    "web": {"kind": "daemon", "enabled": True, "active": True},
    "docs": {"kind": "daemon", "enabled": True, "active": False},
    "preview": {"kind": "daemon", "enabled": True, "active": False},
    "spare": {"kind": "daemon", "enabled": False, "active": False},
    "site": {"kind": "daemon", "enabled": True, "active": True},
    "loose": {"kind": "daemon", "enabled": True, "active": False},
    "other": {"kind": "daemon", "enabled": True, "active": False},
}}
GIVEN = [{"key": "atlas-api", "root": "/home/me/code/atlas-api", "lead": None, "associated": []},
         {"key": "kestrel-web", "root": "/home/me/code/kestrel-web", "lead": "kim",
          "associated": []}]
SECTION_LINK = {"text": "Services", "module": "services", "route": ""}
DOCS = {"text": "docs failed", "heavy": True, "rank": 30}
PREVIEW = {"text": "preview is stopped", "warn": True}

# Places services' component in a hidden host on the shell's document, through the client
# as the Projects page places it, and keeps every report it sends in `window.__reports`.
_PLACE = """async ([projects, open]) => {
  const client = await import("/m/ui/client.js");
  window.__reports = [];
  const host = document.createElement("div");
  host.hidden = true;
  document.body.append(host);
  host.addEventListener("moxi-project-facts", (event) => window.__reports.push(event.detail));
  const attrs = {projects: JSON.stringify(projects)};
  if (open !== null) attrs.open = open;
  const element = await client.place(host, "services", "project-facts", attrs);
  window.__facts = element;
  return element === null ? null : element.localName;
}"""

_SET = """([name, value]) => value === null ? window.__facts.removeAttribute(name)
                                    : window.__facts.setAttribute(name, value)"""


@pytest.fixture(scope="module")
def ui_shell_options(tmp_path_factory) -> dict:
    """services' page server acts on the fake supervisor holding `UNITS`."""
    return {"site": [str(fake_site(tmp_path_factory.mktemp("services-units"), UNITS))]}


@pytest.fixture(scope="module")
def seeded(ui_shell) -> None:
    """In atlas-api: `web` running (ok), `docs` on and not running (fail), `preview`
    stopped by hand (warn, stopped) and `spare` off. In kestrel-web: `site` running.
    `loose` names no project and `other` a project the page doesn't give; both fail."""
    seed(ui_shell,
         written("web", project="atlas-api"),
         written("docs", project="atlas-api"),
         written("preview", project="atlas-api",
                 after={"stopped": f"{STOPPED:%Y-%m-%dT%H:%M:%SZ}"}),
         written("spare", project="atlas-api", enabled=False),
         written("site", project="kestrel-web"),
         written("loose"),
         written("other", project="gone"))


def _store_version(shell, version: int | None) -> int:
    """Services' store's schema version; with `version`, set to it first."""
    code = ("import sqlite3, sys\n"
            "from moxi_services import store\n"
            "conn = sqlite3.connect(store.path())\n"
            "if sys.argv[1] != '-': conn.execute(f'PRAGMA user_version = {int(sys.argv[1])}')\n"
            "conn.commit()\n"
            "print(conn.execute('PRAGMA user_version').fetchone()[0])\n")
    return int(python(shell, code, "-" if version is None else str(version)))


def _reported(page, check, what: str) -> Any:
    """Wait until the component's last report passes `check`; return it."""
    deadline = time.monotonic() + WAIT_S
    last = None
    while time.monotonic() < deadline:
        reports = page.evaluate("() => window.__reports")
        if reports:
            last = reports[-1]
            if check(last):
                return last
        page.wait_for_timeout(50)
    raise AssertionError(f"the last report is not {what}: {json.dumps(last)}")


def _atlas_section(report: dict) -> dict:
    return report["projects"]["atlas-api"]["sections"][0]


def test_it_reports_services_verdicts_for_the_given_projects_and_follows_the_topic(
        seeded, ui_shell, ui_page):
    page = ui_page
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url))
    assert page.evaluate(_PLACE, [GIVEN, None]) == "moxi-services-project-facts"

    # Closed: each given project with a row; failed rows first, then the warning.
    report = _reported(page, lambda r: r is not None and r["projects"]["atlas-api"].get("parts")
                       == [DOCS, PREVIEW], "atlas-api's failed docs and stopped preview")
    assert (report["version"], report["order"], report["gathers"]) == (1, 60, "services")
    assert report["projects"] == {"atlas-api": {"parts": [DOCS, PREVIEW]}, "kestrel-web": {}}

    # Opened: the `Services` row, ranked while a row fails, its lines and its quiet line.
    page.evaluate(_SET, ["open", "atlas-api"])
    report = _reported(page, lambda r: "sections" in r["projects"]["atlas-api"], "opened")
    section = _atlas_section(report)
    assert (section["label"], section["rank"], section["link"]) == ("Services", 30, SECTION_LINK)
    lines = [[part["text"] for part in line["parts"]] for line in section["lines"]]
    assert lines[:2] == [["docs failed", "not running"], ["preview is stopped"]], lines
    assert len(lines) == 3 and lines[2][0].startswith("web running") and lines[2][1] == "spare off"
    assert "sections" not in report["projects"]["kestrel-web"]

    # A row added under kestrel-web comes by the topic, with no read of the route again.
    reads = sum(1 for url in asked if "/m/services/api/rows" in url)
    seed(ui_shell, written("api", project="kestrel-web"))
    report = _reported(page, lambda r: r["projects"]["kestrel-web"].get("parts")
                       == [{"text": "api failed", "heavy": True, "rank": 30}], "api failed")
    assert sum(1 for url in asked if "/m/services/api/rows" in url) == reads

    # A store services can't read reports null; the next good one puts the facts back.
    version = _store_version(ui_shell, None)
    _store_version(ui_shell, version + 1)
    try:
        _reported(page, lambda r: r is None, "null")
    finally:
        _store_version(ui_shell, version)
    _reported(page, lambda r: r is not None and _atlas_section(r)["rank"] == 30, "the facts again")

    # Removed, it reports nothing more, whatever changes.
    count = page.evaluate("() => { window.__facts.remove(); return window.__reports.length; }")
    seed(ui_shell, written("late", project="atlas-api"))
    page.wait_for_timeout(QUIET_S * 1000)
    assert page.evaluate("() => window.__reports.length") == count

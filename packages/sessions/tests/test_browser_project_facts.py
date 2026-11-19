"""sessions' `project-facts` in a browser, through a real shell that starts sessions' and
projects' real page servers, on three installs:

- **both**: core, ui, projects and sessions. Placed alone, the component reports each given
  project's newest activity and sessions active recently, the opened one's `Sessions` row
  and `Stays` line, and the folder worked in this week that no project holds, reading only
  sessions' own route. On the Projects page those facts are drawn: `1 active recently`,
  `last active`, `not used yet`, the order by activity, `Not a project` and the opened
  `Sessions` row.
- **no sessions**: core, ui and projects. The Projects page draws no sessions fact and
  leaves no gap: no line two, no time, no folder group.
- **no projects**: core, ui and sessions. The component's route answers `off`, and it
  reports null: nothing to draw.

Every other module of this environment is left out of each shell and hidden from every
process it starts (`sitecustomize.py` on the shell's site), so a page server reads it as not
installed. Projects are added with `moxi projects add` in the shell's environment; the
sessions are written by `moxi_sessions.testing`'s builders under the roots it names.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect

from moxi import modules
from moxi_sessions.testing import ClaudeTranscript

pytestmark = [pytest.mark.browser, pytest.mark.timeout(180),
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="sessions' facts are checked in a browser on Linux; the "
                                        "Windows and macOS runs are owed")]

#: How long a test waits for the component to report, in seconds: the ui kit's bound.
WAIT_S = pytest.importorskip("moxi_ui.testing", reason="ui is not installed").WAIT_S
HOUR = 3600.0
DAY = 24 * HOUR

_HIDE = '''"""sessions' facts tests wrote this: in each process started with this directory on
its path, the distributions named here are not found, so their modules read as not
installed."""

from importlib.metadata import Distribution

HIDDEN = frozenset({names!r})
_discover = Distribution.discover


def _found(cls, **kwargs):
    return (dist for dist in _discover(**kwargs)
            if (dist.name or "").lower().replace("_", "-") not in HIDDEN)


Distribution.discover = classmethod(_found)
'''

# Places sessions' component in a hidden host on the shell's document, through the client
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
  const element = await client.place(host, "sessions", "project-facts", attrs);
  return element === null ? null : element.localName;
}"""


def _options(base: Path, keep: tuple[str, ...], *, shadow: tuple[str, ...] = ()) -> dict[str, Any]:
    """A shell holding core, ui and `keep`, with Claude Code's and Codex's roots under
    `base`. Each module of `shadow` gets, on the shell's site, a distribution of its name
    that declares no module: the core's loader, which reads `.dist-info` directories off
    the path in order and takes the first, finds it there and reads that module as absent."""
    gone = [found.name for found in modules.scan() if found.name not in ("ui", *keep)]
    site = base / "site"
    site.mkdir(parents=True)
    (site / "sitecustomize.py").write_text(
        _HIDE.format(names={f"moxi-{name}" for name in gone}), encoding="utf-8")
    for name in shadow:
        info = site / f"moxi_{name}-0.0.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: moxi-{name}\nVersion: 0.0.0\n",
                                       encoding="utf-8")
        (info / "entry_points.txt").write_text("", encoding="utf-8")
    (base / "claude-home" / "projects").mkdir(parents=True)
    (base / "codex-home" / "sessions").mkdir(parents=True)
    return {"site": [str(site)], "leave_out": gone,
            "env": {"CLAUDE_CONFIG_DIR": str(base / "claude-home"),
                    "CODEX_HOME": str(base / "codex-home")}}


def _home(shell) -> Path:
    return Path(shell.env["HOME"])


def _add_project(shell, key: str) -> str:
    """Add project `key` at `~/code/<key>` through projects' own verb; its root."""
    root = _home(shell) / "code" / key
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "moxi", "projects", "add", key, "--root", str(root)],
                   env=dict(shell.env), check=True, capture_output=True, timeout=60)
    return str(root)


def _session(shell, n: int, cwd: str, at: float, *, program: bool = False) -> str:
    """A Claude Code session in `cwd`, last changed at `at`; its claim."""
    session_id = f"0190b000-0000-7000-8000-{n:012x}"
    t = ClaudeTranscript(Path(shell.env["CLAUDE_CONFIG_DIR"]) / "projects", session_id=session_id,
                         cwd=cwd, project=cwd.replace("/", "-"),
                         entrypoint="sdk-cli" if program else "cli")
    t.user("Do the thing.", origin=None if program else "human")
    t.assistant("Done.")
    stamp = int(at * 1_000_000_000)
    os.utime(t.write(), ns=(stamp, stamp))
    return f"claude/{session_id}"


@pytest.fixture(scope="module")
def both(ui_shells, tmp_path_factory) -> dict[str, Any]:
    """atlas-api with a session ten minutes old, one two days old and a program's; kestrel-web
    with one three days old; quiet with none; and a session in `~/scratch`, no project's."""
    shell = ui_shells(**_options(tmp_path_factory.mktemp("both"), ("projects", "sessions")))
    roots = {key: _add_project(shell, key) for key in ("atlas-api", "kestrel-web", "quiet")}
    now = time.time()
    scratch = str(_home(shell) / "scratch")
    seeded = {
        "now": now, "roots": roots, "scratch": scratch,
        "recent": _session(shell, 1, roots["atlas-api"], now - 600),
        "older": _session(shell, 2, roots["atlas-api"], now - 2 * DAY),
        "program": _session(shell, 3, roots["atlas-api"], now - 300, program=True),
        "kestrel": _session(shell, 4, roots["kestrel-web"], now - 3 * DAY),
        "loose": _session(shell, 5, scratch, now - 2 * HOUR),
    }
    return {"shell": shell, **seeded}


def _given(roots: dict[str, str]) -> list[dict[str, Any]]:
    return [{"key": key, "root": root, "lead": None, "associated": []} for key, root in roots.items()]


def _reported(page, wanted, what: str) -> Any:
    """Wait until `wanted` holds of the component's last report; return it."""
    deadline = time.monotonic() + WAIT_S
    last = None
    while time.monotonic() < deadline:
        reports = page.evaluate("() => window.__reports")
        if reports:
            last = reports[-1]
            if wanted(last):
                return last
        page.wait_for_timeout(50)
    raise AssertionError(f"the last report is not {what}: {json.dumps(last)}")


def test_alone_it_reports_each_projects_sessions_and_the_work_dir_in_no_project(both, ui_contexts):
    page = ui_contexts(both["shell"]).pages[0]
    asked: list[str] = []
    page.on("request", lambda request: asked.append(request.url))
    assert page.evaluate(_PLACE, [_given(both["roots"]), "atlas-api"]) == "moxi-sessions-project-facts"

    report = _reported(page, lambda r: r is not None, "a report")

    now = both["now"]
    assert (report["version"], report["order"], report["activity"], report["gathers"]) == (
        1, 30, "all", "sessions")
    atlas = report["projects"]["atlas-api"]
    assert atlas["at"] == pytest.approx(now - 300, abs=1)       # the program's is the newest
    assert atlas["parts"] == [{"text": "", "claims": [both["recent"]],
                               "count": {"one": "active recently", "many": "active recently"}}]
    (section,) = atlas["sections"]
    assert section["label"] == "Sessions"
    assert section["link"] == {"text": "Sessions", "module": "sessions", "route": ""}
    assert section["lines"][0]["parts"][0]["claims"] == [both["recent"], both["older"]]
    assert section["lines"][0]["parts"][0]["count"]["after_many"] == "more this week"
    assert atlas["stays"] == ["its 3 sessions and their transcripts"]
    assert report["projects"]["kestrel-web"] == {"at": pytest.approx(now - 3 * DAY, abs=1)}
    assert "quiet" not in report["projects"]
    assert report["folders"] == [{"folder": both["scratch"], "at": pytest.approx(now - 2 * HOUR, abs=1),
                                  "parts": [{"text": "1 session this week"}]}]
    assert report["more_folders"] == 0
    assert [url for url in asked if "/api/" in url and "/m/sessions/api/" not in url] == []


def test_the_projects_page_draws_sessions_facts(both, ui_contexts):
    page = ui_contexts(both["shell"]).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    expect(panel.locator('[data-part="facts"] moxi-sessions-project-facts')).to_have_count(1)

    def row(key: str):
        return panel.locator(f'section[data-part="projects"] moxi-ui-row[data-key="{key}"]')

    expect(row("atlas-api").locator(".l2")).to_have_text("1 active recently")
    expect(row("atlas-api")).to_have_attribute("fact", re.compile("^last active "))
    expect(row("kestrel-web")).to_have_attribute("fact", re.compile("^last active "))
    expect(row("kestrel-web").locator(".l2")).to_be_hidden()
    expect(row("quiet")).to_have_attribute("fact", "not used yet")
    keys = panel.locator('section[data-part="projects"] moxi-ui-row')
    expect(keys.first).to_have_attribute("data-key", "atlas-api")
    assert keys.evaluate_all("rows => rows.map(row => row.dataset.key)") == [
        "atlas-api", "kestrel-web", "quiet"]

    group = panel.locator('section[data-part="folders"]')
    expect(group.locator('[data-line="folders"]')).to_have_text("Not a project")
    loose = group.locator("moxi-ui-row")
    expect(loose).to_have_count(1)
    expect(loose).to_have_attribute("name", "~/scratch")
    expect(loose).to_have_attribute("where", "1 session this week")

    row("atlas-api").locator('[slot="acts-one"]').click()
    opened = row("atlas-api").locator('[data-part="opened"] [data-label="Sessions"]')
    expect(opened.locator(".ol")).to_contain_text("2 sessions this week")
    link = opened.get_by_role("link", name="Sessions")
    expect(link).to_have_attribute("href", "/m/sessions/")


def test_without_sessions_the_projects_page_draws_no_sessions_fact_and_no_gap(ui_shells,
                                                                             tmp_path_factory,
                                                                             ui_contexts):
    shell = ui_shells(**_options(tmp_path_factory.mktemp("no-sessions"), ("projects",)))
    for key in ("atlas-api", "kestrel-web"):
        _add_project(shell, key)
    page = ui_contexts(shell).pages[0]
    page.goto("/m/projects/")
    panel = page.locator("#moxi-panel > moxi-projects-page")
    expect(panel.locator('section[data-part="list"]')).to_have_attribute("data-state", "up")
    rows = panel.locator('section[data-part="projects"] moxi-ui-row')
    expect(rows).to_have_count(2)
    expect(panel.locator('[data-part="facts"] > *')).to_have_count(0)
    expect(panel.locator('section[data-part="folders"]')).to_have_count(0)
    for key in ("atlas-api", "kestrel-web"):
        item = panel.locator(f'moxi-ui-row[data-key="{key}"]')
        expect(item.locator(".l2")).to_be_hidden()
        assert not item.get_attribute("fact")


def test_without_projects_the_component_reports_null(ui_shells, tmp_path_factory, ui_contexts):
    shell = ui_shells(**_options(tmp_path_factory.mktemp("no-projects"), ("sessions",),
                                 shadow=("projects",)))
    _session(shell, 1, str(_home(shell) / "code" / "atlas-api"), time.time() - 60)
    page = ui_contexts(shell).pages[0]
    given = [{"key": "atlas-api", "root": str(_home(shell) / "code" / "atlas-api"), "lead": None,
              "associated": []}]
    assert page.evaluate(_PLACE, [given, "atlas-api"]) == "moxi-sessions-project-facts"

    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline and not page.evaluate("() => window.__reports.length"):
        page.wait_for_timeout(50)
    assert page.evaluate("() => window.__reports") == [None]

"""The Projects page's browser tests: the two installs they run on, seven projects seeded
into a shell's store, and the `fixture` module whose `project-facts` reports what a test
tells it to (tests/fixture/moxi_fixture).

Two installs, each a test shell over a site of its own:

- **the fixture install**: core, ui, projects, notes and the fixture;
- **the small install**: core, ui and projects.

Every other module of this environment is left out of the shell's list and hidden from
every process the shell starts: the site's `sitecustomize.py` keeps their distributions
from being found, so a page server reads them as not installed and loads none of their
links with projects.

The store is written in this process at the place the shell's environment gives it, so
the page server reads what the test wrote. A shell is given the core's record of a
terminal with its own environment, as a person's own terminal run would leave it, so the
page's writes are not refused.

The fixture's page server answers the report in `report.json` in its state directory, or
null while there is none; `say` writes it, `fail` makes the read fail, `hold` makes its
link with projects refuse every remove, and `reads` counts the reads it answered.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from moxi import modules

SOURCE = Path(__file__).resolve().parent / "fixture" / "moxi_fixture"

#: The modules each install holds besides core and ui.
FIXTURE_INSTALL = ("projects", "notes", "fixture")
SMALL_INSTALL = ("projects",)


class Row(NamedTuple):
    """One seeded project: its key, root and notes root under the shell's home, kind,
    lead and associated agents."""

    key: str
    root: str
    kind: str
    lead: str | None
    associated: tuple[str, ...]
    notes_root: str


#: Seven projects of every shape the list draws (a lead and agents, a missing folder, an
#: agent's home), in key order.
SEVEN = (
    Row("atlas-api", "code/atlas-api", "project", "builder-1", ("planner", "reviewer"),
        "notes/code/atlas-api"),
    Row("harbor-infra", "code/harbor-infra", "project", "ops-watch", (), "notes/code/harbor-infra"),
    Row("kestrel-web", "code/kestrel-web", "project", "builder-2", ("reviewer",),
        "notes/code/kestrel-web"),
    Row("lumen-docs", "code/lumen-docs", "project", "docs-writer", (), "notes/code/lumen-docs"),
    Row("orbit-cli", "code/orbit-cli", "project", "planner", (), "notes/code/orbit-cli"),
    Row("quill-mobile", "code/quill-mobile", "project", "builder-2", (), "notes/code/quill-mobile"),
    Row("reviewer-home", "agents/reviewer", "agent", None, (), "notes/agents/reviewer"),
)
#: The project whose folder is gone.
MISSING = "quill-mobile"
#: A folder no project holds, which the fixture's full report says was worked in.
LOOSE = "code/finch-scripts"

#: The hiding `sitecustomize.py` a site holds.
_HIDE = '''"""projects' page kit wrote this: in each process started with this directory on
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


def left_out(holds: tuple[str, ...]) -> list[str]:
    """Every module of this environment an install holding `holds` (with core and ui)
    leaves out."""
    return [found.name for found in modules.scan() if found.name not in ("ui", *holds)]


def install_fixture(site: Path) -> Path:
    """Copy the fixture's package into `site` with its `.dist-info`: a module, and a link
    with projects. Returns `site`."""
    site.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, site / "moxi_fixture", ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    info = site / "moxi_fixture-0.1.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: moxi-fixture\nVersion: 0.1.0\n",
                                   encoding="utf-8")
    (info / "entry_points.txt").write_text(
        "[moxi.modules]\nfixture = moxi_fixture.module\n\n"
        "[moxi.projects]\nfixture = moxi_fixture.projects_link\n", encoding="utf-8")
    return site


def options(site: Path, *, with_fixture: bool) -> dict[str, Any]:
    """The `ui_shells` options of the fixture install (`with_fixture`) or the small one, over
    `site`, a new directory."""
    holds = FIXTURE_INSTALL if with_fixture else SMALL_INSTALL
    gone = left_out(holds)
    site.mkdir(parents=True, exist_ok=True)
    hidden = sorted(f"moxi-{name}" for name in gone)
    (site / "sitecustomize.py").write_text(_HIDE.format(names=set(hidden)), encoding="utf-8")
    if with_fixture:
        install_fixture(site)
    return {"site": [str(site)], "leave_out": gone}


def record_terminal(shell: Any) -> None:
    """The core's record of a terminal whose environment is the shell's, where the shell's
    page servers read it."""
    from moxi import paths, terminal

    terminal.write(dict(shell.env), path=paths.terminal_record(shell.env))


def home(shell: Any) -> Path:
    return Path(shell.env["HOME"])


def root(shell: Any, row: Row) -> str:
    """`row`'s root, absolute."""
    return str(home(shell) / row.root)


def seed(shell: Any, rows: tuple[Row, ...] = SEVEN, *, with_notes: bool = True,
         missing: tuple[str, ...] = (MISSING,)) -> None:
    """Make the shell's store hold `rows` and nothing else, each folder made but those of
    `missing`, which are taken away, and the loose folder made. With `with_notes` each row keeps
    its notes root (an install with notes)."""
    from moxi_projects import store

    for row in rows:
        where = Path(root(shell, row))
        if row.key in missing:
            shutil.rmtree(where, ignore_errors=True)
        else:
            where.mkdir(parents=True, exist_ok=True)
    (home(shell) / LOOSE).mkdir(parents=True, exist_ok=True)

    def fill(conn) -> None:
        conn.execute("DELETE FROM project_agents")
        conn.execute("DELETE FROM projects")
        for row in rows:
            store.insert(conn, key=row.key, root=root(shell, row), kind=row.kind, lead=row.lead,
                         notes_root=str(home(shell) / row.notes_root) if with_notes else None)
            for name in row.associated:
                store.add_agent(conn, row.key, name)

    store.write(fill, where=store.path(shell.env))


def given(shell: Any, rows: tuple[Row, ...] = SEVEN) -> list[dict[str, Any]]:
    """The `projects` attribute the page gives each facts component for `rows`."""
    return [{"key": row.key, "root": root(shell, row), "lead": row.lead,
             "associated": list(row.associated)} for row in rows]


# --- the fixture ---------------------------------------------------------------------------

def _state(shell: Any) -> Path:
    where = shell.state_dir("fixture")
    where.mkdir(parents=True, exist_ok=True)
    return where


def say(shell: Any, report: Any) -> None:
    """From the fixture's next read on, it reports `report` (None for nothing to say)."""
    target = _state(shell) / "report.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(report), encoding="utf-8")
    os.replace(temp, target)


def _flag(shell: Any, name: str, *, enabled: bool) -> None:
    flag = _state(shell) / name
    if enabled:
        flag.write_text("", encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)


def fail(shell: Any, *, enabled: bool = True) -> None:
    """While on, the fixture's read is refused, so its component reports null."""
    _flag(shell, "fail", enabled=enabled)


def hold(shell: Any, *, enabled: bool = True) -> None:
    """While on, the fixture's link with projects refuses to remove any project, with
    `held_sentence(key)`."""
    _flag(shell, "hold", enabled=enabled)


def held_sentence(key: str) -> str:
    return f"the fixture holds {key}; let it go on the fixture's page"


def reads(shell: Any) -> int:
    """How many reads the fixture's page servers have answered or refused."""
    try:
        return len((_state(shell) / "reads").read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return 0


def reset(shell: Any) -> None:
    """The fixture says nothing, its read works and it holds nothing."""
    say(shell, None)
    fail(shell, enabled=False)
    hold(shell, enabled=False)


#: How long ago each project was last active in the full report, in seconds.
AGO_S = {"atlas-api": 60, "kestrel-web": 60, "orbit-cli": 180, "lumen-docs": 540,
         "harbor-infra": 1920, "reviewer-home": 5700}
#: How long ago the loose folder was last worked in, in seconds.
LOOSE_AGO_S = 2 * 24 * 3600
#: The fixture's `order` in the full report.
ORDER = 100
#: The route a section's link opens on the fixture's page, for `key`.
LINK_ROUTE = "project/{key}"


def noon() -> float:
    """Today's local noon, in epoch seconds: a `now` for the full report whose projects'
    times, at most `AGO_S` before it, all fall on its own local day."""
    return datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


def _running(*sessions: str) -> dict[str, Any]:
    return {"text": "", "count": {"one": "running", "many": "running"},
            "claims": list(sessions)}


def _active(*sessions: str) -> dict[str, Any]:
    return {"text": "", "count": {"one": "active recently", "many": "active recently"},
            "claims": list(sessions)}


def _link(key: str) -> dict[str, str]:
    return {"text": "Fixture", "module": "fixture", "route": LINK_ROUTE.format(key=key)}


def full_report(shell: Any, now: float | None = None) -> dict[str, Any]:
    """A report with a fact of every kind the contract carries, for the seven projects:
    `at`, heavy ranked parts, warnings, counted parts with claims, plain parts, ranked and
    plain sections with links and a count after lines, agents that run and that don't,
    Remove's lines, a loose folder, `activity` `all` and `gathers`. quill-mobile has
    nothing."""
    now = time.time() if now is None else now
    at = {key: int(now - ago) for key, ago in AGO_S.items()}
    week = {"text": "", "claims": ["claude/t1", "claude/t2", "claude/e6"],
            "count": {"one": "session this week", "many": "sessions this week",
                      "after_one": "more this week", "after_many": "more this week"}}
    stays = ["the sessions run here and their transcripts"]
    projects = {
        "atlas-api": {
            "at": at["atlas-api"],
            "parts": [{"text": "queue-worker failed at 18:31", "heavy": True, "rank": 30},
                      _running("claude/s2", "claude/s8"), {"text": "$8.49 today"}],
            "sections": [
                {"label": "Services", "rank": 30, "link": _link("atlas-api"),
                 "lines": [{"parts": [{"text": "queue-worker failed at 18:31", "heavy": True},
                                      {"text": "api-server running"}]}]},
                {"label": "Sessions", "link": _link("atlas-api"),
                 "lines": [{"parts": [{"text": "planner-Nov-08-2026 12:45"},
                                      {"text": "slot 3"}], "claims": ["claude/s2"]},
                           {"parts": [{"text": "builder-1-Nov-08-2026 11:00"},
                                      {"text": "slot 4"}], "claims": ["claude/s8"]},
                           {"parts": [week]}]},
                {"label": "Cost",
                 "lines": [{"parts": [{"text": "$8.49 today"}, {"text": "$15.84 in 7 days"},
                                      {"text": "$21.49 in 30 days"}, {"text": "an estimate"}]}]},
            ],
            "agents": {"builder-1": {"runs": True, "state": "running now", "where": "in slot 4"}},
            "stays": stays,
        },
        "harbor-infra": {"at": at["harbor-infra"],
                         "parts": [_running("codex/s3"), {"text": "$3.30 today"}]},
        "kestrel-web": {
            "at": at["kestrel-web"],
            "parts": [_running("claude/s1", "claude/s9"), {"text": "$3.29 today"}],
            "sections": [{"label": "Sessions", "link": _link("kestrel-web"),
                          "lines": [{"parts": [{"text": "kestrel-web-sonnet-Nov-08 08:20"},
                                               {"text": "slot 1"}], "claims": ["claude/s1"]}]}],
            "agents": {"builder-2": {"runs": False, "state": "not running now"}},
            "stays": stays,
        },
        "lumen-docs": {"at": at["lumen-docs"],
                       "parts": [{"text": "a git hook broke at 17:55", "heavy": True, "rank": 30},
                                 {"text": "docs-preview is stopped", "warn": True},
                                 _active("opencode/s5"), {"text": "$0.36 today"}]},
        "orbit-cli": {"at": at["orbit-cli"],
                      "parts": [{"text": "dep-audit's last run failed at 18:00", "warn": True},
                                _active("claude/s6"), {"text": "$1.55 today"}]},
        "reviewer-home": {"at": at["reviewer-home"],
                          "parts": [_running("claude/s7"), {"text": "$2.55 today"}]},
    }
    loose = {"folder": str(home(shell) / LOOSE), "at": int(now - LOOSE_AGO_S),
             "parts": [{"text": "2 sessions this week"}, {"text": "$0.82"}]}
    return {"version": 1, "order": ORDER, "activity": "all", "gathers": "sessions",
            "projects": projects, "folders": [loose], "more_folders": 0}

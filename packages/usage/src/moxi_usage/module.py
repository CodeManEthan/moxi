"""The announcement of Moxi's usage module, which moxi.modules reads."""

#: Interface 2 is interface 1 plus `report`'s `work_dir` filter, the `projects` report by
#: folder, `saving` on a `terms` total and `opened` on a limits row. Interface 3 is 2 plus
#: the stop: its stamps' names, `cost_multiple` and `threshold` at a multiple `<k>x`. A
#: caller that needs any of those asks for 2 or 3; one written against 1 still loads.
INTERFACES = frozenset({1, 2, 3})
API = "moxi_usage.api"
CLI = "moxi_usage.cli:main"
SUMMARY = ("How much of each plan window is used, how full a session's context is, and "
           "what the tokens cost, from each harness's own records.")
OPTIONAL = {
    "turns": "Claude limits, Codex limits from the app-server, the warn-at mark, the "
             "worker ceiling and the stop",
    "projects": "the token index records each session's project, so the projects report "
                "and --project keep one project across worktrees and harnesses",
    "ui": "the usage page, with its limits and cost blocks and its row on Home, in the "
          "browser UI shell",
}
GATED = {}
#: The launcher stamps these per launch; usage reads them in the session's listener.
INTERNAL_ENV = {
    "MOXI_USAGE_WARN_AT": "launcher stamp: the launch's warn-at mark",
    "MOXI_USAGE_CROSSING_AT": "launcher stamp: the mark whose crossing goes to crossing "
                              "handlers",
    "MOXI_USAGE_STOP_AT": "launcher stamp: the launch's stop mark, a multiple <k>x",
    "MOXI_USAGE_STOP_HANDOFFS": "launcher stamp: the handoff folder a stopped session may "
                                "still write in",
}
#: Per-session variables a launcher stamps for usage, so it scrubs them from what a launch
#: inherits.
SESSION_ENV = {
    "MOXI_USAGE_WARN_AT": "launcher stamp: the launch's warn-at mark",
    "MOXI_USAGE_CROSSING_AT": "launcher stamp: the mark whose crossing goes to crossing "
                              "handlers",
    "MOXI_USAGE_STOP_AT": "launcher stamp: the launch's stop mark, a multiple <k>x",
    "MOXI_USAGE_STOP_HANDOFFS": "launcher stamp: the handoff folder a stopped session may "
                                "still write in",
}
SETTINGS = "settings.toml"
#: The install question: the worker ceiling's switch, asked as "usage: <text>? [y/N]".
QUESTIONS = {
    "ceiling.enabled": "the worker ceiling (warn a subagent, then refuse its tools except to "
                  "hand off, once its context grows past ceiling.warn_tokens and "
                  "ceiling.hard_tokens)",
}
SETUP = None
#: The status line segment: Claude's plan limits from the status line's own input.
SEGMENT = "moxi_usage.segment"
#: usage's page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_usage.page:PAGE"
#: What usage offers the browser UI shell: its panel, two blocks for the modules view, and
#: its row on Home, which reads `/api/summary`.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Usage", "contract": 1, "place": "page",
             "group": "daily", "order": 30,
             "doc": "Tokens and cost by day, session, project and model, with the limits"},
    "limits": {"script": "limits.js", "title": "Limits", "contract": 1, "place": "block",
               "home": True,
               "doc": "Each plan window: how much is used, when it resets and when it fills"},
    "cost": {"script": "cost.js", "title": "Cost", "contract": 1, "place": "block",
             "home": True, "doc": "Cost and tokens today and over the last 7 days"},
    "row": {"script": "row.js", "title": "Usage", "contract": 1, "place": "block",
            "home": True, "home_as": "row", "order": 300,
            "doc": "How full the plan windows are and today's cost, on one line"},
    "project-facts": {"script": "project-facts.js", "title": "Usage",
                      "doc": "Each project's cost, for the Projects page",
                      "contract": 1, "place": "inline",
                      "attributes": {"projects": {"type": "string",
                                                  "doc": "the projects the page holds, JSON"},
                                     "open": {"type": "string",
                                              "doc": "the key of the opened project"}}},
}
# The announcement's version, and the optional names it declares.
ANNOUNCES = 1
# The config file's whole check: the `[[model]]` override rows hold more rules than the
# schema's list type can say.
CONFIG_CHECK = "moxi_usage.settings:check"
#: What usage keeps, for `moxi paths usage`, the doctor's checks and `moxi uninstall
#: --purge`. Each is made by its first write. The index and the model snapshot can be
#: made again (a rebuild, `moxi usage prices update`); the restart-point measure by
#: `moxi usage restart-point --measure`. Limits readings, fired marks, the ceiling's
#: markers and the stop's latches are history no harness file holds whole, so nothing
#: rebuilds them. The index's
#: lock is declared beside it so `--purge` takes it with the index.
STORES = {
    "index": {"root": "state", "path": "index.db", "kind": "sqlite", "private": True,
              "retention": "keep", "rebuildable": True, "lazy": True},
    "index_lock": {"root": "state", "path": "index.lock", "kind": "file", "private": True,
                   "retention": "keep", "rebuildable": True, "lazy": True},
    "readings": {"root": "state", "path": "readings.db", "kind": "sqlite", "private": True,
                 "retention": "8d", "rebuildable": False, "lazy": True},
    "marks": {"root": "state", "path": "marks", "kind": "dir", "private": True,
              "retention": "7d", "rebuildable": False, "lazy": True},
    "ceiling": {"root": "state", "path": "ceiling", "kind": "dir", "private": True,
                "retention": "7d", "rebuildable": False, "lazy": True},
    "stop": {"root": "state", "path": "stop", "kind": "dir", "private": True,
             "retention": "7d", "rebuildable": False, "lazy": True},
    "restart_point": {"root": "state", "path": "restart-point.json", "kind": "file",
                      "private": True, "retention": "keep", "rebuildable": True,
                      "lazy": True},
    "snapshot": {"root": "data", "path": "models-snapshot.json", "kind": "file",
                 "private": True, "retention": "keep", "rebuildable": True, "lazy": True},
}
#: usage's own checks: the model table's age and gaps, the token index, Codex's imports,
#: the restart-point measure, the sweep of fired marks, ceiling markers and stop latches,
#: and what usage registers with turns.
DOCTOR = "moxi_usage.doctor:rows"
#: The weekly restart-point measure, off until `restart_point.measure_weekly` is turned
#: on. A timer runs every few minutes or hours, or at set times on set days; this one
#: runs once a week.
TIMERS = {"measure": {"command": "moxi-usage-measure", "at": ["04:30"], "days": ["mon"],
                      "switch": "restart_point.measure_weekly",
                      "doc": "measure the restart point from the last 21 days"}}
#: No daemon, so no idle CPU, memory or fd budget; a hook may cost the agent at most twice
#: the floor, `python -I -c pass`.
BUDGETS = {"hook_ms": 2.0}

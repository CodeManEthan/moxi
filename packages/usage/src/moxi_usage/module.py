"""The announcement of Moxi's usage module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = "moxi_usage.api"
CLI = "moxi_usage.cli:main"
SUMMARY = ("How much of each plan window is used, how full a session's context is, and "
           "what the tokens cost, from each harness's own records.")
OPTIONAL = {
    "turns": "Claude limits, Codex limits from the app-server, the warn-at mark and the "
             "worker ceiling",
    "projects": "the token index records each session's project, so the projects report "
                "and --project keep one project across worktrees and harnesses",
}
GATED = {}
#: The launcher stamps these per launch; usage reads them in the session's listener.
INTERNAL_ENV = {
    "MOXI_USAGE_WARN_AT": "launcher stamp: the launch's warn-at mark",
    "MOXI_USAGE_CROSSING_AT": "launcher stamp: the mark whose crossing goes to crossing "
                              "handlers",
}
#: Per-session variables a launcher stamps for usage, so it scrubs them from what a launch
#: inherits.
SESSION_ENV = {
    "MOXI_USAGE_WARN_AT": "launcher stamp: the launch's warn-at mark",
    "MOXI_USAGE_CROSSING_AT": "launcher stamp: the mark whose crossing goes to crossing "
                              "handlers",
}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
# The announcement's version, and the optional names it declares.
ANNOUNCES = 1
# The config file's whole check: the `[[model]]` override rows hold more rules than the
# schema's list type can say.
CONFIG_CHECK = "moxi_usage.settings:check"
#: What usage keeps, for `moxi paths usage`, the doctor's checks and `moxi uninstall
#: --purge`. Each is made by its first write. The index and the model snapshot can be
#: made again (a rebuild, `moxi usage prices update`); the restart-point measure by
#: `moxi usage restart-point --measure`. Limits readings, fired marks and the ceiling's
#: markers are history no harness file holds whole, so nothing rebuilds them.
STORES = {
    "index": {"root": "state", "path": "index.db", "kind": "sqlite", "private": True,
              "retention": "keep", "rebuildable": True, "lazy": True},
    "readings": {"root": "state", "path": "readings.db", "kind": "sqlite", "private": True,
                 "retention": "8d", "rebuildable": False, "lazy": True},
    "marks": {"root": "state", "path": "marks", "kind": "dir", "private": True,
              "retention": "7d", "rebuildable": False, "lazy": True},
    "ceiling": {"root": "state", "path": "ceiling", "kind": "dir", "private": True,
                "retention": "7d", "rebuildable": False, "lazy": True},
    "restart_point": {"root": "state", "path": "restart-point.json", "kind": "file",
                      "private": True, "retention": "keep", "rebuildable": True,
                      "lazy": True},
    "snapshot": {"root": "data", "path": "models-snapshot.json", "kind": "file",
                 "private": True, "retention": "keep", "rebuildable": True, "lazy": True},
}
#: usage's own checks: the model table's age and gaps, the token index, Codex's imports,
#: the restart-point measure and whether turns is installed.
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

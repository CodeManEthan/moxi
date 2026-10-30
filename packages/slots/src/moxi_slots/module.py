"""The announcement of Moxi's slots module, which moxi.modules reads.

Standard library only, and imports nothing of slots: it loads on every system, native
Windows included, where slots does not run.
"""

INTERFACES = frozenset({1})
#: The module other code calls.
API = "moxi_slots.api"
#: What `moxi slots` runs, in process, as `main(argv, prog="moxi slots")`.
CLI = "moxi_slots.cli:main"
SUMMARY = 'Numbered tmux sessions to launch into, on one supervised tmux server.'
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
#: Records tmux's absolute path, which every slots process uses.
SETUP = "moxi_slots.setup:setup"
#: Refuses an uninstall while any slot is occupied or not idle: stopping the server ends
#: every session in every slot.
TEARDOWN = "moxi_slots.setup:teardown"
SEGMENT = None
#: The tmux server, in the foreground under the supervisor. Stopping it ends every
#: session in every slot, so an install or upgrade rewrites its unit but never restarts
#: a running one. Not on Windows, where slots does not run.
DAEMONS = {"server": {"command": "moxi-slots-server", "args": [], "stop": "group",
                      "doc": "the tmux server that holds the slots",
                      "platforms": ["linux", "darwin"], "upgrade": "keep"}}
HEALTH = {"server": "moxi_slots.health:check"}
DOCTOR = "moxi_slots.doctor:rows"
#: The registry holds only what tmux can't know; the launch files hold a launch's
#: environment until the trampoline takes it; the mismatched screen regions are kept to be
#: read and added as fixtures (slots itself keeps the last 20: a retention here is a size
#: or an age); a server started on demand logs to server.log.
STORES = {"registry": {"root": "state", "path": "slots.json", "kind": "file",
                       "private": True, "retention": "keep", "rebuildable": False,
                       "lazy": True},
          "mismatches": {"root": "state", "path": "mismatches", "kind": "dir",
                         "private": True, "retention": "keep", "rebuildable": True,
                         "lazy": True},
          "launch": {"root": "runtime", "path": "launch", "kind": "dir", "private": True,
                     "retention": "keep", "rebuildable": True, "lazy": True},
          "server-log": {"root": "state", "path": "server.log", "kind": "file",
                         "private": True, "retention": "10M", "rebuildable": True,
                         "lazy": True}}
#: The server's idle CPU, as a share of one core.
BUDGETS = {"idle_cpu": 0.03}

"""The announcement of Moxi's services module, which moxi.modules reads.

services keeps one list of every daemon and timer on the machine: the ones modules
declare, commands the user adds, and units already on the system the user adopts. A
command the user adds becomes a unit through the core's writer, from services' run-time
declarations (`UNITS_FROM`). It runs no daemon or timer of its own.
"""

INTERFACES = frozenset()
API = None
#: What `moxi services` runs, in process, as `main(argv, prog="moxi services")`.
CLI = "moxi_services.cli:main"
SUMMARY = "Moxi's services: one list of every daemon and timer on the machine, with start, stop, enable, disable, add, remove and watch."
OPTIONAL = {
    "projects": "Project keys on services, and projects' remove check: a project is not "
                "removed while a service still names it.",
}
GATED = {}
INTERNAL_ENV = {}
#: The config schema: one on/off switch per adopted Linux timer, by its row's name.
SETTINGS = "settings.toml"
#: Run again by `moxi install services`, the upgrade path: it migrates an existing
#: store and makes none.
SETUP = "moxi_services.lifecycle:setup"
#: Run before `moxi uninstall services` removes anything: it refuses while a service the
#: user added still has its unit here, and gives every adopted Linux timer back as it was.
TEARDOWN = "moxi_services.lifecycle:teardown"
SEGMENT = None
ANNOUNCES = 1
#: The user's written rows, as run-time declarations the core's writer makes units from.
UNITS_FROM = "moxi_services.units:declared"
#: The registry of the user's rows. The first add makes it, so the core's doctor reads
#: it as fine while it is missing. It holds what the user typed, so nothing can rebuild
#: it. The gate's run lines and logs in services' state directory are the core's stores.
STORES = {"registry": {"root": "data", "path": "services.db", "kind": "sqlite",
                       "private": True, "retention": "keep", "rebuildable": False,
                       "lazy": True}}
#: services' own checks: its store, each user row's verdict and the project keys.
DOCTOR = "moxi_services.doctor:rows"

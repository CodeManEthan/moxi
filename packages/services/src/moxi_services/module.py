"""The announcement of Moxi's services module, which moxi.modules reads.

services keeps one list of every daemon and timer on the machine: the ones modules
declare, commands the user adds, and units already on the system the user adopts. A
command the user adds becomes a unit through the core's writer, from services' run-time
declarations (`UNITS_FROM`). It runs no daemon or timer of its own.
"""

INTERFACES = frozenset()
API = None
CLI = None
SUMMARY = "Moxi's services: one list of every daemon and timer on the machine, with start, stop, enable, disable, add, remove and watch."
OPTIONAL = {
    "projects": "Project keys on services, and projects' remove check: a project is not "
                "removed while a service still names it.",
}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = None
SETUP = None
SEGMENT = None
ANNOUNCES = 1
#: The user's written rows, as run-time declarations the core's writer makes units from.
UNITS_FROM = "moxi_services.units:declared"

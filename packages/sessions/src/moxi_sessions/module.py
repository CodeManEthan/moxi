"""The announcement of Moxi's sessions module, which moxi.modules reads."""

INTERFACES = frozenset({2})
API = "moxi_sessions.api"
CLI = "moxi_sessions.cli:main"
SUMMARY = ("Read each harness's own session records in one shape: find a session and its "
           "subagents, list a root's sources, read records forward or back.")
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
# The announcement's version, and the optional names it declares. sessions keeps no state
# of its own, so it declares no store; it runs no process, so no daemon, timer or health
# check. Its doctor rows check each harness's root.
ANNOUNCES = 1
STORES = {}
DOCTOR = "moxi_sessions.doctor:rows"

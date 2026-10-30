"""The announcement of Moxi's cache module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = "moxi_cache.api"
CLI = "moxi_cache.cli:main"
SUMMARY = "Know when a session's prompt cache goes cold, and act before it does."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = "moxi_cache.segment"
# Its doctor rows: what `moxi doctor` adds for cache.
DOCTOR = "moxi_cache.doctor:rows"
#: The watcher, which `moxi install cache` writes as a unit through the core's writer.
#: `stop = "process"`: a stop or restart ends the watcher alone, never the asks it started,
#: which run detached and outlive it. `moxi cache service` still writes today's unmarked
#: unit, as it always has.
DAEMONS = {"watch": {"command": "moxi-cache-watch",
                     "doc": "the watcher that keeps clock records of live Claude sessions",
                     "stop": "process"}}

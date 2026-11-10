"""The announcement of Moxi's cache module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = "moxi_cache.api"
CLI = "moxi_cache.cli:main"
SUMMARY = "Know when a session's prompt cache goes cold, and act before it does."
OPTIONAL = {"ui": "The cache panel and its chip in the browser UI shell."}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = "moxi_cache.segment"
# Its doctor rows: what `moxi doctor` adds for cache.
DOCTOR = "moxi_cache.doctor:rows"
#: The watcher, which `moxi install cache` writes as a unit through the core's writer.
#: `stop = "process"`: a stop or restart ends the watcher alone, never the asks it started,
#: which run detached and outlive it.
DAEMONS = {"watch": {"command": "moxi-cache-watch",
                     "doc": "the watcher that keeps clock records of live Claude sessions",
                     "stop": "process"}}
#: cache's page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_cache.page:PAGE"
#: What cache offers the browser UI shell: its panel, and a chip any host may place.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Cache", "contract": 1, "place": "page",
             "group": "daily", "order": 50,
             "doc": "Each live session's prompt cache, warm or cold"},
    "chip": {"script": "chip.js", "title": "Cache chip", "contract": 1, "place": "inline",
             "doc": "One session's cache: warm, near or cold, and the time left",
             "attributes": {"session": {"type": "string", "required": True,
                                        "doc": "the harness's session id"}}},
}

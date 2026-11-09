"""The announcement of Moxi's statusline module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = None
CLI = "moxi_statusline.cli:main"
SUMMARY = ("A Claude Code status line: your directory, branch, model and context, with the "
           "figures installed modules add.")
OPTIONAL = {"ui": "the status line page: pick, order and preview the line"}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
#: The whole check of the config file's text: the schema, then the rules of `show` and the
#: two context bands.
CONFIG_CHECK = "moxi_statusline.settings:check"
SETUP = None
SEGMENT = None
#: Its doctor rows: what `moxi doctor` adds for statusline.
DOCTOR = "moxi_statusline.doctor:rows"
#: The old `statusLine` value kept at a switch, the answer setup records, and the old bytes
#: of Claude's `settings.json` before each write. None of them can be rebuilt.
STORES = {
    "previous": {"root": "state", "path": "previous.json", "kind": "file",
                 "private": True, "retention": "keep", "rebuildable": False, "lazy": True},
    "asked": {"root": "state", "path": "asked.json", "kind": "file",
              "private": True, "retention": "keep", "rebuildable": False, "lazy": True},
    "backups": {"root": "state", "path": "backups", "kind": "dir",
                "private": True, "retention": "keep", "rebuildable": False, "lazy": True},
}
#: One render of the default line, as a multiple of the interpreter's own start
#: (`python -I -c pass`): about 30 ms at a 10 ms start.
BUDGETS = {"hook_ms": 3.0}

"""The announcement of fixture, the page module the ui module's tests install.

It has a page, a block for the shell's home, an inline component with a required
attribute, and one component written for a contract the shell doesn't place yet, which
the modules view lists as refused. Its settings hold every type and a switch table,
`rows`, whose entry `backup` only the timer below names (no test installs the timer).
"""

INTERFACES = frozenset({1})
API = None
CLI = None
SUMMARY = "A page module the UI's tests install"
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
TIMERS = {"backup": {"command": "moxi-fixture-backup", "every": "1h", "switch": "rows.backup",
                     "doc": "a backup no test installs"}}
PAGE = "moxi_fixture.page:PAGE"
COMPONENTS = {
    "page": {"script": "page.js", "title": "Fixture", "doc": "The fixture's panel",
             "contract": 1, "place": "page"},
    "block": {"script": "block.js", "title": "Fixture count", "doc": "The fixture's count",
              "contract": 1, "place": "block", "home": True},
    "tag": {"script": "tag.js", "title": "Fixture tag", "doc": "One label",
            "contract": 1, "place": "inline",
            "attributes": {"label": {"type": "string", "required": True,
                                     "doc": "the text the tag shows"}}},
    "later": {"script": "later.js", "title": "Later", "doc": "Written for a later contract",
              "contract": 2, "place": "block"},
}

"""The announcement of Moxi's ui module, which moxi.modules reads."""

#: 1 is the component contract this module's shell places, and its own components are
#: written for. 2 is 1 plus the API: the check a page part makes before it writes. 3 is 2
#: plus `api.loopback`, the page-server base every page module is built on.
INTERFACES = frozenset({1, 2, 3})
#: The announcement schema this file is written to: 2 lets a socket daemon's `start` follow
#: a bool setting.
ANNOUNCES = 2
#: Imports only the standard library, since every `moxi.modules.load("ui")` imports it.
API = "moxi_ui.api"
CLI = "moxi_ui.cli:main"
SUMMARY = "The browser shell: one origin for every module's page"
#: The `testing` extra brings only what moxi_ui.testing's browser fixtures need.
OPTIONAL = {"testing": "Playwright and pytest for moxi_ui.testing, the plugin a module's "
                       "own tests use to run its pages in a test shell and a browser.",
            "gateway": "Passkeys for the gateway, the UI on your phone through the tunnel.",
            "tunnel": "The tunnel, which gives the gateway the address a phone reaches."}
GATED = {}
#: The gateway's state: its devices, its key and its audit log (one file per UTC day, kept
#: 30 days by the gateway itself). And the doctor run the shell keeps, which the next run
#: writes again.
STORES = {"gateway": {"root": "state", "path": "gateway", "kind": "dir", "private": True,
                      "retention": "keep", "rebuildable": False, "lazy": True},
          "checks": {"root": "state", "path": "checks.json", "kind": "file", "private": True,
                     "retention": "keep", "rebuildable": True, "lazy": True}}
#: The one variable of ui's prefix it reads outside moxi.paths and moxi.config, and why.
INTERNAL_ENV = {"MOXI_UI_LOCKED_SETTINGS": "set by whoever starts the shell, for one run: "
                                           "the settings its pages may not change"}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
#: The shell. A socket holds its port on systemd and launchd, so the shell starts on the
#: first browser connection, and at login too while the gateway is on (`gateway.enabled`),
#: since a phone may call at any time; turning it on or off restarts nothing. On Windows a
#: logon task starts it at every logon either way, and it holds the port from logon to
#: logout.
DAEMONS = {"shell": {"command": "moxi-ui-shell", "doc": "the UI shell",
                     "platforms": ["linux", "darwin", "win32"],
                     "socket": {"setting": "port", "addresses": ["127.0.0.1", "::1"],
                                "start": {"login": "gateway.enabled"}}}}
HEALTH = {"shell": "moxi_ui.health:check"}
#: The shell and each page server it started; the shell's budget is compared there, on its
#: own pid, so the module declares no `BUDGETS` (the core would count its page servers).
DOCTOR = "moxi_ui.doctor:rows"
#: The shell's own page, which it serves in process; no page server runs for it.
PAGE = "moxi_ui.page:PAGE"
#: The ui panel (Home, the modules view and the settings), the modules view's block, and
#: Home's Health row, which the shell shows last among the rows that need nothing.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Modules",
             "doc": "Every installed module, its page server and its settings",
             "contract": 1, "place": "page"},
    "servers": {"script": "servers.js", "title": "Page servers",
                "doc": "Each module's page server and its state",
                "contract": 1, "place": "block", "home": True},
    "health": {"script": "health.js", "title": "Health",
               "doc": "Whether the doctor's checks pass, and what to run for one that fails",
               "contract": 1, "place": "block", "home": True, "home_as": "row", "order": 900,
               "attributes": {"open": {"type": "bool",
                                       "doc": "set by Home while the row is shown opened"}}},
}

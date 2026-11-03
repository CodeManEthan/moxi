"""The announcement of Moxi's ui module, which moxi.modules reads."""

#: The component contract this module's shell places, and its own components are written
#: for.
INTERFACES = frozenset({1})
#: The announcement schema this file is written to: 2 lets a socket daemon's `start` follow
#: a bool setting.
ANNOUNCES = 2
API = None
CLI = "moxi_ui.cli:main"
SUMMARY = "The browser shell: one origin for every module's page"
#: The `testing` extra brings only what moxi_ui.testing's browser fixtures need.
OPTIONAL = {"testing": "Playwright and pytest for moxi_ui.testing, the plugin a module's "
                       "own tests use to run its pages in a test shell and a browser.",
            "gateway": "Passkeys for the gateway, the UI on your phone through the tunnel.",
            "tunnel": "The tunnel, which gives the gateway the address a phone reaches."}
GATED = {}
#: The gateway's state: its devices, its key and its audit log (one file per UTC day, kept
#: 30 days by the gateway itself).
STORES = {"gateway": {"root": "state", "path": "gateway", "kind": "dir", "private": True,
                      "retention": "keep", "rebuildable": False, "lazy": True}}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
#: The shell. A socket holds its port on systemd and launchd, so the shell starts on the
#: first browser connection, and at login too while the gateway is on (`gateway.enabled`),
#: since a phone may call at any time; turning it on or off restarts nothing. On Windows a
#: logon task starts it at every logon either way, and it holds the port from logon to
#: logout.
DAEMONS = {"shell": {"command": "moxi-ui-serve", "doc": "the UI shell",
                     "platforms": ["linux", "darwin", "win32"],
                     "socket": {"setting": "port", "addresses": ["127.0.0.1", "::1"],
                                "start": {"login": "gateway.enabled"}}}}
HEALTH = {"shell": "moxi_ui.health:check"}
#: The shell and each page server it started; the shell's budget is compared there, on its
#: own pid, so the module declares no `BUDGETS` (the core would count its page servers).
DOCTOR = "moxi_ui.doctor:rows"
#: The shell's own page, which it serves in process; no page server runs for it.
PAGE = "moxi_ui.page:PAGE"
#: The modules view: the ui panel and its home block.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Modules",
             "doc": "Every installed module, its page server and its settings",
             "contract": 1, "place": "page"},
    "servers": {"script": "servers.js", "title": "Page servers",
                "doc": "Each module's page server and its state",
                "contract": 1, "place": "block", "home": True},
}

"""The announcement of Moxi's ui module, which moxi.modules reads."""

#: The component contract this module's shell places, and its own components are written
#: for.
INTERFACES = frozenset({1})
API = None
CLI = "moxi_ui.cli:main"
SUMMARY = "The browser shell: one origin for every module's page"
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
#: The shell. A socket holds its port on systemd and launchd, so the shell starts on the
#: first browser connection; on Windows a logon task starts it and it holds the port from
#: logon to logout.
DAEMONS = {"shell": {"command": "moxi-ui-serve", "doc": "the UI shell",
                     "platforms": ["linux", "darwin", "win32"],
                     "socket": {"setting": "port", "addresses": ["127.0.0.1", "::1"]}}}
HEALTH = {"shell": "moxi_ui.health:check"}
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

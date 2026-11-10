"""The announcement of Moxi's comms module, which moxi.modules reads.

Standard library only, and imports nothing of comms: the umbrella reads it to list the
module, dispatch `moxi comms`, offer its extras and check its contract.
"""

INTERFACES = frozenset({2})
#: The module other code calls: sending, the reads, the names, `api.tokens`.
API = "moxi_comms.api"
#: What `moxi comms` runs, in process, as `main(argv, prog="moxi comms")`.
CLI = "moxi_comms.cli:main"

SUMMARY = 'Messages between coding-agent sessions, delivered into their turns.'
OPTIONAL = {
    "codex": "Codex sessions as comms agents: moxi-comms-codex launches each one "
             "through turns' Codex host, which needs the websockets package.",
    "cache": "The cold-send hold: mail to an agent whose prompt cache went cold waits for "
             "its next turn, and a cache comms cannot read refuses the send. Without it "
             "every agent reads as having no cache and gets its mail live.",
    "ui": "The comms panel and its roster of agents in the browser UI shell.",
    "connector": "comms' tools in the Claude apps, text chat and voice mode, through the "
                 "public MCP host",
    "services": "One list of every daemon and timer: with it, a doctor row whose cure is a "
                "restart of comms' daemon names `moxi services restart comms/daemon`.",
}
GATED = {"moxi-comms-codex": "codex"}
#: Each variable of comms' prefix it reads outside moxi.paths and moxi.config, and why.
INTERNAL_ENV = {
    "MOXI_COMMS_NAME": "wrapper stamp: the session's chosen identity name",
    "MOXI_COMMS_SCOPE": "wrapper stamp: the session's identity scope",
    "MOXI_COMMS_GLOBAL": "wrapper stamp: the identity is the global one",
    "MOXI_COMMS_DIRECTORS": "wrapper stamp: the directors this launch grants",
    "MOXI_COMMS_SOCKET": "wrapper stamp: the daemon socket the session was launched "
                         "against",
    "MOXI_COMMS_CLAIM_SOCKET": "wrapper stamp: the claim socket the session's adapter "
                               "answers",
    "MOXI_COMMS_PROJECT": "wrapper stamp and override: the session's project key",
    "MOXI_COMMS_MCP_TOKEN": "wrapper stamp: this session's token for comms' tools",
    "MOXI_COMMS_MCP_URL": "wrapper stamp: the address of comms' tools for this session",
    "MOXI_COMMS_SHELL_REACH": "wrapper stamp: off when this session's shell can't reach "
                              "comms",
    "MOXI_COMMS_OPERATOR": "wiring: the operator lane's user id for one send",
    "MOXI_COMMS_SECRET_": "the secrets chain's env step: one credential per variable",
    "MOXI_COMMS_MOCK_BACKEND": "test seam: the mock record's JSON store",
}
#: The variables comms' wrappers stamp per session for comms' tools, which a launcher
#: scrubs before every launch so no session inherits another's. comms reads them too, so
#: each is also in INTERNAL_ENV.
SESSION_ENV = {
    "MOXI_COMMS_MCP_TOKEN": "this session's token for comms' tools",
    "MOXI_COMMS_MCP_URL": "the address of comms' tools for this session",
    "MOXI_COMMS_SHELL_REACH": "off when this session's shell can't reach comms",
}
SETTINGS = "settings.toml"
#: Provisioning at install, through the store's one opener: a comms 0.2 home whose files
#: the move refuses fails the install with the reason.
SETUP = "moxi_comms.provision:setup"
#: comms' own doctor rows: the move of comms 0.2's files into comms.db and what it left,
#: and the reserved send-only names.
DOCTOR = "moxi_comms.checks:doctor_rows"
SEGMENT = None
#: comms' whole loader over a config text alone, run by `moxi config set`, `unset` and
#: `check`, so the verb never writes a file comms would refuse.
CONFIG_CHECK = "moxi_comms.config:check_text"
#: What comms keeps, for `moxi paths comms`, the doctor's checks and `moxi uninstall
#: --purge`. comms.db is the one SQLite store; the rest are folders a holder or an
#: adapter makes on first use.
STORES = {
    "comms": {"root": "state", "path": "comms.db", "kind": "sqlite", "private": True,
              "retention": "keep", "rebuildable": False},
    "cards": {"root": "data", "path": "cards", "kind": "dir", "private": False,
              "retention": "keep", "rebuildable": False, "lazy": True},
    "tokens": {"root": "state", "path": "tokens", "kind": "dir", "private": True,
               "retention": "keep", "rebuildable": False, "lazy": True},
    "codex": {"root": "state", "path": "codex", "kind": "dir", "private": True,
              "retention": "keep", "rebuildable": False, "lazy": True},
    "opencode": {"root": "state", "path": "opencode", "kind": "dir", "private": True,
                 "retention": "keep", "rebuildable": False, "lazy": True},
}
#: The delivery daemon, which `moxi install comms` writes as a unit through the core's
#: writer.
#: Its socket is the loopback port of comms' tools, `[mcp] port`: the supervisor holds it
#: while the daemon restarts, so a session that calls in that gap waits instead of being
#: refused, and no other local user can take the port meanwhile. The daemon still starts
#: at login and after a crash (`start = "login"`): adapters, wrappers and the CLI reach it
#: on its Unix socket, which nothing activates.
DAEMONS = {"daemon": {"command": "moxi-comms-daemon", "doc": "the delivery daemon",
                      "socket": {"setting": "mcp.port", "addresses": ["127.0.0.1"],
                                 "start": "login"}}}
#: The daemon's health: whether it answers on its socket, and its tools' endpoint.
HEALTH = {"daemon": "moxi_comms.checks:health"}
#: What the daemon may cost at idle, `moxi doctor --costs`: the HTTP app of comms' tools
#: adds about 53 MB and one listening descriptor to a daemon of about 21 MB and 14, and
#: no wakeups. Descriptors grow by one per live binding and by keep-alives that close
#: after 5 s, so 64 holds about 45 live sessions.
BUDGETS = {"idle_cpu": 0.01, "rss": "150M", "fds": 64}
#: comms' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_comms.page:PAGE"
#: What comms offers the browser UI shell: its panel, and the roster any home view or
#: page may place.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Comms", "contract": 1, "place": "page",
             "group": "daily", "order": 40,
             "doc": "Who can be messaged, and who waits on whom"},
    "roster": {"script": "roster.js", "title": "Agents", "contract": 1, "place": "block",
               "home": True, "doc": "Names held by live sessions, and standing names"},
}

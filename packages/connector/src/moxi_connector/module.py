"""The announcement of Moxi's connector module, which moxi.modules reads.

Standard library only, and imports nothing of the connector: the umbrella reads it to list
the module, offer its extras and check its contract.

The connector is the public MCP door for the Claude apps: one HTTPS address, its own
sign-in, and the tools other modules provide through the group
`moxi.connector.providers` (`moxi_connector.provider`). It is off until its switch is on.
"""

#: Interface 1, `moxi_connector.provider`: what a module gives to serve its tools. A
#: provider that has the connector only through an extra reaches it through
#: `moxi.modules.load("connector", interface=1).api`, never by importing it.
INTERFACES = frozenset({1})
API = "moxi_connector.provider"
#: `moxi connector`: on, off, status, check, log, grants and revoke.
CLI = "moxi_connector.cli:main"
SUMMARY = ("The public MCP door for the Claude apps: the listener, the sign-in, the address "
           "and the tools other modules provide.")
OPTIONAL = {
    "tunnel": "The public address, from Tailscale Funnel or a Cloudflare named tunnel",
}
GATED = {}
#: The connector reads no variable of its prefix: no setting declares one.
INTERNAL_ENV = {}
#: The config schema, governing CONFIG/connector/config.toml.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text: the schema, then the public URL, the
#: port, the redirect URIs and the sign-in's lifetime.
CONFIG_CHECK = "moxi_connector.settings:check"
SETUP = None
SEGMENT = None
#: The door, behind its switch. Its socket unit holds 127.0.0.1:<port> while the process
#: restarts, so a request in that gap waits in the backlog and no other local user can
#: bind the port and collect bearer tokens. It starts at login while the switch is on.
DAEMONS = {"server": {"command": "moxi-connector-server",
                      "doc": "the public MCP door for the Claude apps",
                      "switch": "enabled",
                      "socket": {"setting": "port", "addresses": ["127.0.0.1"],
                                 "start": "login"}}}
#: The door's health: its control socket answers `status` within 2 s.
HEALTH = {"server": "moxi_connector.checks:health"}
#: The connector's doctor rows: the switch, the process, the address, the port, the edge,
#: the providers, the mint gate, the grants and each provider's own. While the switch is
#: off they are quiet but for a tunnel entry forwarding to the port nothing holds.
DOCTOR = "moxi_connector.doctor:rows"
#: What the door may cost at idle, `moxi doctor --costs`. Measured with no provider: about
#: 76 MB and 8 descriptors after start, and no measurable CPU over 90 s while it woke
#: every 30 s to read its origin. A provider adds its own (comms' a daemon client: one
#: socket and its reader); descriptors grow by one per open request (at most 32) and by
#: keep-alives, closed after 5 s.
BUDGETS = {"idle_cpu": 0.01, "rss": "150M", "fds": 64}
#: What the connector keeps, for `moxi paths connector`, the doctor's checks and `moxi
#: uninstall --purge`: the call log, a file per UTC day, and the sign-in's grants. Each is
#: made by its first write, and neither can be made again.
STORES = {
    "calls": {"root": "state", "path": "calls", "kind": "dir", "private": True,
              "retention": "keep", "rebuildable": False, "lazy": True},
    "grants": {"root": "state", "path": "grants", "kind": "dir", "private": True,
               "retention": "keep", "rebuildable": False, "lazy": True},
}

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
    "MOXI_COMMS_OPERATOR": "wiring: the operator lane's user id for one send",
    "MOXI_COMMS_SECRET_": "the secrets chain's env step: one credential per variable",
    "MOXI_COMMS_MOCK_BACKEND": "test seam: the mock record's JSON store",
}
SETTINGS = "settings.toml"
SETUP = None
#: comms' own doctor rows: the move of comms 0.2's files into comms.db, and what it left.
DOCTOR = "moxi_comms.store.upgrade_0_2:doctor_rows"
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
#: writer. `moxi comms service` still writes today's unmarked unit, as it always has.
DAEMONS = {"daemon": {"command": "moxi-comms-daemon", "doc": "the delivery daemon"}}

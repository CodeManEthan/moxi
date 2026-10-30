"""The announcement of Moxi's usage module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = "moxi_usage.api"
CLI = "moxi_usage.cli:main"
SUMMARY = ("How much of each plan window is used, how full a session's context is, and "
           "what the tokens cost, from each harness's own records.")
OPTIONAL = {
    "turns": "Claude limits, Codex limits from the app-server, the warn-at mark and the "
             "worker ceiling",
}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
# The announcement's version, and the optional names it declares.
ANNOUNCES = 1
# The config file's whole check: the `[[model]]` override rows hold more rules than the
# schema's list type can say.
CONFIG_CHECK = "moxi_usage.settings:check"

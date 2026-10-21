"""The announcement of Moxi's sessions module, which moxi.modules reads."""

INTERFACES = frozenset({1})
API = "moxi_sessions.api"
CLI = "moxi_sessions.cli:main"
SUMMARY = "Read a harness's own session records: find a session, read its transcript back."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None

"""The announcement of Moxi's loopback module, which moxi.modules reads.

loopback is a library: it has no command and no settings.
"""

INTERFACES = frozenset({1})
API = "moxi_loopback.api"
CLI = None
SUMMARY = "The base every Moxi page server is built on: the request check, the route table, the runtime file and the server."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = None
SETUP = None
SEGMENT = None

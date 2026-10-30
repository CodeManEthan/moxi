"""The announcement of Moxi's loopback module, which moxi.modules reads.

loopback is a library: it has no command and no settings.
"""

INTERFACES = frozenset({1})
API = "moxi_loopback.api"
CLI = None
SUMMARY = "The page-server base every Moxi page is built on: the request check, runtime files and route tables."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = None
SETUP = None
SEGMENT = None

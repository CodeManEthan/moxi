"""The announcement of Moxi's loopback module, which moxi.modules reads.

The base now lives in ui, as `moxi_ui.loopback`; this announcement hands a page module
that still loads `loopback` the same API module ui's interface 3 gives, so both name one
module object. It has no command and no settings.
"""

INTERFACES = frozenset({1})
API = "moxi_ui.loopback.api"
CLI = None
SUMMARY = "The base every Moxi page server is built on: the request check, the route table, the runtime file and the server."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = None
SETUP = None
SEGMENT = None

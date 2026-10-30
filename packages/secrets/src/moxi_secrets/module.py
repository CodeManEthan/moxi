"""The announcement of Moxi's secrets module, which moxi.modules reads.

secrets is a library with no command, daemon or store: other modules import
`moxi_secrets` and call it.
"""

from moxi_secrets import engine as _engine

ANNOUNCES = 1
INTERFACES = frozenset({1})
API = "moxi_secrets"
CLI = None
SUMMARY = "Named pattern sets for credentials, catch-alls, private names and personal data, and one way to scan text for them."
OPTIONAL = {
    "re2": "the fast engine: every pattern on RE2, so no text is held back as unscanned",
    "personal-data": "the personal-data set: payment cards, IBANs and US SSNs, checked by python-stdnum",
}
# The fast engine is offered only where a google-re2 wheel installs; anywhere else an
# install would try to build the C++ library from source.
if not _engine.re2_wheel_fits():
    del OPTIONAL["re2"]
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
DOCTOR = "moxi_secrets.doctor:rows"

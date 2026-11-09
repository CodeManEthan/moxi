"""The announcement of Moxi's memory module, which moxi.modules reads.

So far it declares the config schema and the two optional links. The command, the API,
the listeners' work, setup, teardown, the doctor and the ingest timer are added here as
they are built.
"""

ANNOUNCES = 1
INTERFACES = frozenset()
API = None
CLI = None
SUMMARY = ("Search past sessions: a stemmed keyword index of every harness's transcripts, "
           "read where the harness keeps them")
OPTIONAL = {
    "comms": "Each session's comms identity at start, so the start lists show an agent "
             "its own recent sessions and name the agent behind each project session.",
    "launcher": "Each session's launch kind, so scratch launches stay out of default "
                "search and out of the start lists.",
}
GATED = {}
INTERNAL_ENV = {}
#: The config schema; every key is read at its use.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text: the schema, then the rules it can't say.
CONFIG_CHECK = "moxi_memory.settings:check_text"
SETUP = None
SEGMENT = None

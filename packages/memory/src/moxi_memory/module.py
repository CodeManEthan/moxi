"""The announcement of Moxi's memory module, which moxi.modules reads.

memory keeps a stemmed keyword index of every harness's session transcripts, read where
each harness keeps them. Its verb is `moxi memory` and its library is
`moxi_memory.api`. Through turns it gives every session one start line, two lists of
recent sessions and a short reminder at each prompt, and starts its ingest child as
sessions run. comms and the launcher are optional links.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1})
API = "moxi_memory.api"
#: What `moxi memory` runs, in process, as `main(argv, prog="moxi memory")`.
CLI = "moxi_memory.cli:main"
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
#: Says which harness roots hold how many sessions and how long each harness keeps them,
#: offers to turn Claude Code's auto memory off, and offers the first full ingest.
SETUP = "moxi_memory.setup:setup"
#: Leaves Claude Code's settings as they are, and says so.
TEARDOWN = "moxi_memory.setup:teardown"
DOCTOR = "moxi_memory.doctor:rows"
#: The timer the core writes once its switch is on: a catch-up walk outside agent
#: sessions, for a machine where sessions end rarely or Codex runs outside turns.
TIMERS = {"ingest": {"command": "moxi-memory-ingest", "args": ["--walk"], "every": "1h",
                     "switch": "ingest.timer.enabled",
                     "doc": "read the transcripts that changed since the last walk"}}
#: The session table, which holds each session's identity and launch kind and can't be
#: rebuilt from the transcripts; and the index folder, which holds the index generations,
#: their locks and the pointer, and is rebuilt with `moxi memory reindex`.
STORES = {
    "sessions": {"root": "state", "path": "state.db", "kind": "sqlite", "private": True,
                 "retention": "keep", "rebuildable": False, "lazy": True},
    "index": {"root": "cache", "path": "index", "kind": "dir", "private": True,
              "retention": "keep", "rebuildable": True, "lazy": True},
}
SEGMENT = None

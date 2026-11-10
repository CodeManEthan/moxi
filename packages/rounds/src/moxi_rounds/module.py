"""The announcement of Moxi's rounds module, which moxi.modules reads.

It offers interface 1, the read API in `moxi_rounds.api`, and the verb `moxi rounds`.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1})
API = "moxi_rounds.api"
#: What `moxi rounds` runs, in process, as `main(argv, prog="moxi rounds")`.
CLI = "moxi_rounds.cli:main"
SUMMARY = ("A piece of work tracked from design to landing: its questions, rulings, "
           "builds and gates.")
OPTIONAL = {
    "notes": "Gives the default notes folder of a project that names none, for a "
             "relative rounds folder.",
}
GATED = {}
#: rounds reads its root locators through `moxi.paths` and no other variable.
INTERNAL_ENV = {}
#: rounds stamps no variable on a session.
SESSION_ENV = {}
#: The config schema; every key is read at its use.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text.
CONFIG_CHECK = "moxi_rounds.settings:check_text"
#: The module's own doctor checks: git, each project's rounds folder, each log, the index.
DOCTOR = "moxi_rounds.doctor:rows"
SETUP = None
SEGMENT = None
#: The one store: a cache of what the rounds' logs say, made by the first write and built
#: again from the logs whenever it is missing. The logs themselves are the user's files,
#: in the folders the user chose, and the lock files hold nothing, so neither is a store.
STORES = {"index": {"root": "data", "path": "index.db", "kind": "sqlite", "private": True,
                    "retention": "keep", "rebuildable": True, "lazy": True}}

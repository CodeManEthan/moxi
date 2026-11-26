"""The announcement of Moxi's rounds module, which moxi.modules reads.

It offers interface 1, the read API in `moxi_rounds.api`, the verb `moxi rounds`, and
with ui its page in the browser UI shell.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1})
API = "moxi_rounds.api"
#: What `moxi rounds` runs, in process, as `main(argv, prog="moxi rounds")`.
CLI = "moxi_rounds.cli:main"
SUMMARY = ("A piece of work tracked from design to landing: its questions, rulings, "
           "builds and gates.")
OPTIONAL = {
    "ui": "The rounds page in the browser UI shell: every open round as a line of its "
          "steps.",
    "notes": "Gives the default notes folder of a project that names none, for a "
             "relative rounds folder.",
    "turns": "Runs the leftover sweep at a session's start, when sweep.at_start is on, "
             "and the watched-paths guard, when watched_paths_guard.enabled is on.",
    "comms": "The merge queue's lander tells builders, leads and the project's director "
             "what became of their builds and of main, under the send-only name rounds. "
             "Without it, moxi rounds next and moxi rounds queue are the only places "
             "that news shows.",
}
GATED = {}
#: The install's question: whether to turn on the watched-paths guard.
QUESTIONS = {
    "watched_paths_guard.enabled": "the watched-paths guard (one line after an agent "
                                   "writes a file a project's watched_paths name, with "
                                   "the skills to load; it never refuses, and turns "
                                   "enforces it)",
}
#: The send-only comms name the lander's messages go out under.
RESERVED_NAMES = ["rounds"]
#: rounds reads its root locators through `moxi.paths` and no other variable.
INTERNAL_ENV = {}
#: rounds stamps no variable on a session.
SESSION_ENV = {}
#: The config schema; every key is read at its use.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text.
CONFIG_CHECK = "moxi_rounds.settings:check_text"
#: The module's own doctor checks: git, each project's rounds folder, each log, the
#: index, CI, finish locks and the sweep's dry run.
DOCTOR = "moxi_rounds.doctor:rows"
#: The one shipped skill, moxi-rounds, one subfolder: the judgment a round leaves to its
#: lead and checkers, placed for each harness at install. It holds no command manual,
#: since the verbs print their own commands.
SKILLS = "skills"
SETUP = None
SEGMENT = None
#: The stores: the index, a cache of what the rounds' logs say, made by the first write and
#: built again from the logs whenever it is missing; and the sweep's log, its account of
#: each folder it removed, which nothing else holds, kept to a size. The logs themselves
#: are the user's files, in the folders the user chose, and the lock files hold nothing,
#: so neither is a store.
STORES = {"index": {"root": "data", "path": "index.db", "kind": "sqlite", "private": True,
                    "retention": "keep", "rebuildable": True, "lazy": True},
          "sweeps": {"root": "state", "path": "sweep.jsonl", "kind": "jsonl",
                     "private": True, "retention": "2M", "rebuildable": False,
                     "lazy": True}}
#: rounds' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_rounds.page:PAGE"
#: What rounds offers the browser UI shell: its page.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Rounds", "contract": 1, "place": "page",
             "group": "daily", "order": 45,
             "doc": "Every open round as a line of its steps: what waits on you, the "
                    "builds in flight, and each step's record."},
}

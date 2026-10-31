"""The announcement of Moxi's launcher module, which moxi.modules reads.

Standard library only, and imports nothing of the launcher. The launcher is a library
with a command: `moxi launcher <verb>` runs `cli.main` in its own process, with no
service and no socket of its own. Its page is served by loopback's page server, which the
browser UI shell starts and loopback stops when idle. Every other Moxi module it works with
is an optional link, asked through `moxi.modules.load` where the feature needs it.
"""

#: The API module: `launch`, `check` and `preview`. No interface number is offered yet.
INTERFACES = frozenset()
API = "moxi_launcher.api"
#: What `moxi launcher` runs, in process, as `main(argv, prog="moxi launcher")`.
CLI = "moxi_launcher.cli:main"
SUMMARY = ("Launch Claude Code and Codex sessions from presets over a per-harness flag "
           "catalog, in this terminal, headless or in a tmux slot, and log every launch.")
OPTIONAL = {
    "turns": "Readiness and the first turn: the brief or handoff is delivered as the "
             "session's first turn once it is ready, Codex session ids are learned, a Codex "
             "seat gets its instructions in start context and a seat's waiting handoff is "
             "pointed to at its start. Without it the brief is the harness's opening prompt.",
    "comms": "Identities: a launch may take a comms name, a scope, directors and the "
             "with: posture, and the launcher's console name is reserved. Without it every "
             "launch is a plain harness launch.",
    "slots": "Tmux slots: a launch may start in a numbered tmux session (--target slot, "
             "--slot), and the startup screens it is set to answer are answered there. "
             "Without it a launch starts in this terminal or headless.",
    "projects": "Project keys: a preset, a seat's home or --project may name a project "
                "instead of a path, a launch records the key of the project its directory "
                "is in, and presets --project lists a project's agents beside their seats. "
                "Without it a launch's place is a path or scratch.",
    "ui": "The launcher's page in the browser UI shell: launch from presets over the "
          "catalog, resume past sessions and watch each launched session's state. Without "
          "it the launcher is its command only.",
}
GATED = {}
#: Each Moxi variable the launcher reads outside moxi.paths and moxi.config, and why: the
#: two stamps it sets on the sessions it starts, comms' identity stamp, and the two test
#: seams that let tests stand a module in for turns' and slots' APIs.
INTERNAL_ENV = {
    "MOXI_COMMS_NAME": "comms stamp: the session's comms name, the handoff key of a "
                       "session the launcher did not start",
    "MOXI_LAUNCHER_REQUEST": "launcher stamp: the request id of the launch that started "
                             "this session",
    "MOXI_LAUNCHER_RELAUNCH": "launcher stamp: the session is a relaunch, whose handoff "
                              "comes in its first turn",
    "MOXI_LAUNCHER_KEEP_WARM": "launcher stamp: the launch keeps its session warm, read by "
                               "cache's timer",
    "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "launcher stamp: the launch clears or compacts its "
                                       "session once its cache expires, read by cache's "
                                       "timer",
    "MOXI_LAUNCHER_TURNS": "test seam: the import name of a module that stands in for "
                           "turns' API in the first-turn sender",
    "MOXI_LAUNCHER_SLOTS": "test seam: the import name of a module that stands in for "
                           "slots' API in the slot target",
}
SETTINGS = "settings.toml"
#: Asks the two startup-screen questions when slots is installed.
SETUP = "moxi_launcher.startup:setup"
SEGMENT = None
#: The launcher's page, served by loopback's page server once the UI shell asks for it.
#: Only that server and loopback's export import it.
PAGE = "moxi_launcher.page:PAGE"

# The announcement's version, and the optional names it declares.
ANNOUNCES = 1
#: The launch log, the per-launch run files (prompt files and headless logs) and the
#: handoff folder used when notes is absent. Each is made at its first write. The run
#: files' age is the shipped default of the `runs.keep_days` setting; the sweep that
#: removes them reads the setting, so a user who changes it keeps them that long instead.
STORES = {
    "launches": {"root": "state", "path": "launcher.db", "kind": "sqlite",
                 "private": True, "retention": "keep", "rebuildable": False,
                 "lazy": True},
    "runs": {"root": "state", "path": "runs", "kind": "dir",
             "private": True, "retention": "30d", "rebuildable": True, "lazy": True},
    "handoffs": {"root": "data", "path": "handoffs", "kind": "dir",
                 "private": True, "retention": "keep", "rebuildable": False,
                 "lazy": True},
}
#: The shipped skills, one subfolder each, placed for each harness at install.
SKILLS = "skills"
#: The send-only comms name the launcher's notices go out under.
RESERVED_NAMES = ["console"]
DOCTOR = "moxi_launcher.doctor:rows"
#: The exact stamps the launcher sets on a session, so a launch from inside one scrubs
#: them. Named one by one, not by prefix, so the launcher's own configuration variables
#: are never scrubbed.
SESSION_ENV = {
    "MOXI_LAUNCHER_REQUEST": "the request id of the launch that started the session",
    "MOXI_LAUNCHER_RELAUNCH": "set on a relaunch, so the session's start shows no handoff "
                              "pointer",
    "MOXI_LAUNCHER_KEEP_WARM": "set when the launch keeps its session warm",
    "MOXI_LAUNCHER_RECYCLE_ON_EXPIRY": "set to clear or compact when the launch recycles its "
                                       "session on cache expiry",
    "MOXI_USAGE_CROSSING_AT": "usage's crossing mark, set when the launch recycles at its "
                              "limit; scrubbed even without usage installed",
    "MOXI_USAGE_WARN_AT": "usage's warning mark, set from warn_at; scrubbed even without "
                          "usage installed",
}

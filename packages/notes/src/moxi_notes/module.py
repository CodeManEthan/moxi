"""The announcement of Moxi's notes module, which moxi.modules reads.

It offers interface 1, the API in `moxi_notes.api`: where a session's notes live, its
handoffs folder and the inbox one-offs. Its verb is `moxi notes`.
"""

ANNOUNCES = 1
INTERFACES = frozenset({1})
API = "moxi_notes.api"
#: What `moxi notes` runs, in process, as `main(argv, prog="moxi notes")`.
CLI = "moxi_notes.cli:main"
SUMMARY = 'Where each session keeps its notes, snapshots of the notes repo, and the secret gates that guard it.'
OPTIONAL = {
    "projects": "A project's own notes folder, the topic kind, and projects' remove check: "
                "a project is not removed while that would move its notes.",
}
GATED = {}
#: The one variable notes reads from a session's environment, never from its own API's.
SESSION_ENV = {"MOXI_NOTES_DIR": "the session's notes directory, set by the launcher's "
                                 "notes_dir row"}
INTERNAL_ENV = {"MOXI_NOTES_DIR": "set by the launcher for one launch; read from the "
                                  "session's environment, never by notes' API"}
#: The config schema; every key is read at its use.
SETTINGS = "settings.toml"
#: The module's whole loader over a config text: the schema, then prefix and exclude.
CONFIG_CHECK = "moxi_notes.settings:check_text"
#: The timer the core writes (systemd, launchd or the Task Scheduler) once its switch is
#: on: a snapshot outside agent sessions. It runs one pass and takes no settle duty.
TIMERS = {"snapshot": {"command": "moxi-notes-snapshot", "args": [], "every": "15m",
                       "switch": "snapshot.timer",
                       "doc": "commit a snapshot of the notes outside agent sessions"}}
#: Asks where the notes are and what a commit does with a secret, previews the first
#: snapshot, then registers the gates, the clean filter and the snapshot spec.
SETUP = "moxi_notes.setup:setup"
#: Removes the clean filter; repo's own listener removes the pieces and the spec.
TEARDOWN = "moxi_notes.setup:teardown"
DOCTOR = "moxi_notes.doctor:rows"
SEGMENT = None

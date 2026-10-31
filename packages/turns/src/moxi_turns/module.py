"""The announcement of Moxi's turns module, which moxi.modules reads.

turns is a library with no user command: other modules call `moxi_turns`, and the
internal entry points the harness adapters add are spawned, never typed. Its checks run
in the front door's doctor (`DOCTOR`); it has no daemon, so no `HEALTH`.
"""

INTERFACES = frozenset({2})
API = "moxi_turns"
CLI = None
SUMMARY = ("Drive a coding-agent session's turns: deliver, steer, compact and read a turn "
           "on any harness.")
OPTIONAL = {"codex": "launch Codex sessions through a supervised app-server host"}
GATED = {"moxi-turns-ping": "codex", "moxi-turns-codex-host": "codex"}
INTERNAL_ENV = {
    "MOXI_TURNS_COMMANDS": "a launcher's grant of session commands (clear, exit, compact) "
                           "to the one launch it stamps",
    "MOXI_TURNS_ENDPOINT": "the absolute endpoint command a Claude launch names for "
                           "turns' runner plugin to spawn",
}
#: Per-launch stamps turns' launch additions render, so a launcher scrubs them from what
#: it inherits and stamps its own. turns also reads them, so they stay in INTERNAL_ENV.
SESSION_ENV = {
    "MOXI_TURNS_COMMANDS": "the session commands one launch grants, stamped per launch",
    "MOXI_TURNS_ENDPOINT": "the endpoint command one Claude launch names, stamped per "
                           "launch",
}
SETTINGS = "settings.toml"
SETUP = "moxi_turns.claude_setup:setup"
SEGMENT = None
DOCTOR = "moxi_turns.doctor:check"
#: A hook may cost the agent at most twice the floor, `python -I -c pass`.
BUDGETS = {"hook_ms": 2.0}
STORES = {
    "hook-times": {"root": "state", "path": "hook-times.jsonl", "kind": "jsonl",
                   "retention": "7d", "rebuildable": True, "lazy": True},
}

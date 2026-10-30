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
GATED = {"moxi-turns-ping": "codex"}
INTERNAL_ENV = {
    "MOXI_TURNS_COMMANDS": "a launcher's grant of session commands (clear, exit, compact) "
                           "to the one launch it stamps",
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

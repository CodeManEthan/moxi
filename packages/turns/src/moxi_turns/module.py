"""The announcement of Moxi's turns module, which moxi.modules reads.

turns is a library with no user command: other modules call `moxi_turns`, and the
internal entry points the harness adapters add are spawned, never typed. Its checks run
in the front door's doctor (`DOCTOR`); it has no daemon, so no `HEALTH`. With the `ui`
extra it has a page in the browser UI shell: the live list of the sessions it can reach,
read-only.
"""

INTERFACES = frozenset({2})
API = "moxi_turns"
CLI = None
SUMMARY = ("Drive a coding-agent session's turns: deliver, steer, compact and read a turn "
           "on any harness.")
OPTIONAL = {
    "codex": "launch Codex sessions through a supervised app-server host",
    "ui": "The live sessions panel in the browser UI shell: every session turns can reach, "
          "its turn, its agents and its trouble, read-only.",
}
GATED = {"moxi-turns-ping": "codex", "moxi-turns-codex-host": "codex"}
INTERNAL_ENV = {
    "MOXI_TURNS_COMMANDS": "a launcher's grant of session commands (clear, exit, compact) "
                           "to the one launch it stamps, bound to the launch's session id "
                           "when it names one",
    "MOXI_TURNS_ENDPOINT": "the absolute endpoint command a Claude launch names for "
                           "turns' runner plugin to spawn",
}
#: Per-launch stamps turns' launch additions render, so a launcher scrubs them from what
#: it inherits and stamps its own. turns also reads them, so they stay in INTERNAL_ENV.
SESSION_ENV = {
    "MOXI_TURNS_COMMANDS": "the session commands one launch grants, stamped per launch "
                           "and bound to its session id when it names one",
    "MOXI_TURNS_ENDPOINT": "the endpoint command one Claude launch names, stamped per "
                           "launch",
}
SETTINGS = "settings.toml"
#: The install questions: one per shipped guard, keyed by its switch, each a noun phrase
#: the installer asks as "turns: <text>? [y/N]".
QUESTIONS = {
    "guards.removal": "the removal guard (refuse shell removals Claude Code would stop "
                      "for your approval)",
    "guards.no_nested_agents": "the no-nested-agents guard (stop subagents from starting "
                               "subagents of their own)",
    "guards.question_mode": "question mode (let agents ask you questions only in a turn "
                            "where you typed something about a question)",
}
SETUP = "moxi_turns.claude_setup:setup"
SEGMENT = None
#: turns' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_turns.page:PAGE"
#: What turns offers the browser UI shell: its panel, and no other component.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Live sessions", "contract": 1, "place": "page",
             "doc": "Every running session turns can reach: its turn, its agents and its "
                    "trouble, read-only"},
}
DOCTOR = "moxi_turns.doctor:check"
#: A hook may cost the agent at most twice the floor, `python -I -c pass`.
BUDGETS = {"hook_ms": 2.0}
STORES = {
    "hook-times": {"root": "state", "path": "hook-times.jsonl", "kind": "jsonl",
                   "retention": "7d", "rebuildable": True, "lazy": True},
}

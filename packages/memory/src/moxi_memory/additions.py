"""What memory adds to a launch through turns (the `moxi.turns.additions` group).

The start line tells the agent to run two commands, `moxi memory search` and `moxi
sessions read`, so a launch lets both run without an approval prompt: Claude Code gets
one allowed-tools rule for each, and OpenCode the matching `bash` rules. Codex has no
per-launch rule form, so turns adds nothing there and says so in its note.

The `moxi sessions read` rule also lets `moxi sessions read --all` run: the flag keeps
the easy command small, but an agent that asks for a whole session is not stopped.

The answer is the same for every launch, so it has no side effects and is quick, as the
doctor calls it too. Standard library only.
"""

from __future__ import annotations

#: The commands the start line names, in each harness's own rule syntax.
CLAUDE_RULES = ("Bash(moxi memory search:*)", "Bash(moxi sessions read:*)")
OPENCODE_RULES = ("bash moxi memory search*", "bash moxi sessions read*")


def additions(request) -> dict:
    """memory's contribution to a launch: tool permissions for Claude Code and OpenCode."""
    return {"tool_permissions": {"claude": CLAUDE_RULES, "opencode": OPENCODE_RULES}}


__all__ = ["CLAUDE_RULES", "OPENCODE_RULES", "additions"]

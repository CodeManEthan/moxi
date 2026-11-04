"""The records sessions' browser tests read: one session per harness, with subagents, written
by `moxi_sessions.testing`'s builders under the roots the test shell's environment names.

Claude Code's and Codex's roots are named by `CLAUDE_CONFIG_DIR` and `CODEX_HOME`, which the
shell's `env` option sets; OpenCode's database is under the shell's own `XDG_DATA_HOME`.
The page server the shell starts inherits that environment, so it finds them as it would
in a user's shell.
"""

from __future__ import annotations

from pathlib import Path

from moxi_sessions.testing import ClaudeTranscript, CodexRollout, OpenCodeDb
from moxi_sessions.testing.claude import usage

#: The Claude Code session, with seven turns, a title and a subagent.
SESSION = "0190b000-0000-7000-8000-00000000c1a0"
#: A Claude Code session holding one long message and one the page server cuts.
LONG = "0190b000-0000-7000-8000-00000000c1a1"
#: Its subagent.
AGENT = "a0190b0000000001"
#: The Codex thread, and the subagent thread it spawned.
THREAD = "0190c000-0000-7000-8000-0000000c0de1"
CHILD_THREAD = "0190c000-0000-7000-8000-0000000c0de9"
#: How many turns the Claude Code session holds; the page shows the last five.
TURNS = 7
#: The long message's length in characters, and the cut one's in UTF-8 bytes.
LONG_CHARS = 2500
CUT_BYTES = 80000


def shell_options(base: Path) -> dict:
    """The test shell's options: Claude Code's and Codex's homes under `base`."""
    return {"env": {"CLAUDE_CONFIG_DIR": str(base / "claude-home"),
                    "CODEX_HOME": str(base / "codex-home")}}


def seed(env: dict[str, str]) -> dict[str, str]:
    """Writes every record under the roots `env` names. Returns the OpenCode session's id
    as `opencode` and its child's as `opencode_child`."""
    claude = Path(env["CLAUDE_CONFIG_DIR"]) / "projects"
    t = ClaudeTranscript(claude)
    for n in range(TURNS):
        t.user(f"turn {n}")
        req = t.request(usage_block=usage(input=3, cache_read=900, output=40))
        req.thinking("which file?")
        call = req.tool_use("Read", {"file_path": "/work/project/notes.md"})
        t.tool_result(call, "the notes, which the page never sends")
        req.text(f"done {n}")
        t.turn_duration()
    t.title("fix the tests")
    sub = t.subagent(AGENT, description="Look into the failing test", tool_use_id="toolu_9")
    sub.user("look into it")
    sub.assistant("found it")
    t.write()

    long = ClaudeTranscript(claude, session_id=LONG)
    long.user("x" * LONG_CHARS)
    long.assistant("é" * (CUT_BYTES // 2))
    long.write()

    codex = Path(env["CODEX_HOME"]) / "sessions"
    r = CodexRollout(codex)
    r.turn()
    r.user("codex turn")
    call = r.tool_call("exec", "cat notes.md")
    r.tool_output(call, "the notes, which the page never sends")
    r.request()
    r.assistant("codex done")
    r.turn_end()
    child = r.spawn(CHILD_THREAD, "explore")
    child.turn()
    child.user("explore")
    child.assistant("explored")
    r.write()
    child.write()

    db = OpenCodeDb(Path(env["XDG_DATA_HOME"]) / "opencode")
    s = db.session(title="opencode work")
    s.user("opencode turn")
    a = s.assistant()
    a.step(text="opencode done", input=900, output=40)
    a.complete()
    kid = s.child(title="look into it")
    kid.user("look")
    k = kid.assistant()
    k.step(text="looked", input=5, output=3)
    k.complete()
    db.write()
    return {"opencode": s.id, "opencode_child": kid.id}

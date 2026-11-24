"""The judges of memory's recall-fork guards (`moxi_memory.guards`): a recall's fork may
read and answer, and nothing else, and asks no one.

**Knowing its fork.** A subagent's call is a recall fork's when its agent id is the
`fork` of a recall record of the session, in any state (`fork_record`). While the
session has a `starting` record (its child has not yet written the fork's id), a call
from an agent id no record names waits up to `STARTING_WAIT_S` for the id, and is judged
as that recall's fork when it is still unwritten. Every other subagent's call is left
alone.

**A fork's call (`judge_call`).**

- Once the record is `hurried` or `lost`, the recall is `subconscious.limit_s` past its
  start, or the fork has made `subconscious.max_tools` tool calls over all its rounds
  (the record's `tools`), every call is refused with `TIME_UP`.
- Read, Grep and Glob are allowed.
- A shell call is allowed only when it runs in the foreground under the harness's own
  timeout (no `run_in_background`, no `timeout` of its own) and its command is a plain
  read (`plain_read`) whose every command starts with an entry of
  `subconscious.fork_commands`.
- Every other call (writes, edits, agents, messages, MCP tools, other shell commands)
  is refused with `READ_ONLY`.

Each call allowed is counted on the record, under the session's lock.

**A plain read (`plain_read`).** The test allows what it can see whole and refuses the
rest: every character prints (`str.isprintable`) and none is one of `REFUSED_CHARS`; the
text splits by POSIX quoting with `|` as a word of its own and `#` read as a plain
character, so a comment hides no command from the test; a word of only `|` is a pipe
when it is exactly `|`; no command of the pipeline is empty; and in each command the
first word holds no `=` and the leading words match an entry of the list word for word.

**A fork's permission ask (`judge_ask`).** Refused with `ASK_REFUSED`, so the person
never sees a dialog for it; every other subagent's ask is left to the person.

**Where they run (`guarded`).** Only in a session whose launch turned the subconscious
on, decided once at the session's start.

Standard library at import; the core is imported at use.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Mapping

from . import recalls, records
from .recall_start import mode

#: The refusals.
TIME_UP = "Answer now from what you have: this recall's time is up."
READ_ONLY = "A recall fork only reads: answer from what you have."
ASK_REFUSED = "A recall fork asks no one: answer from what you have."
#: The tools a fork may call freely, and its shell tool.
READ_TOOLS = ("Read", "Grep", "Glob")
SHELL = "Bash"
#: Characters a plain read never holds, quoted or not.
REFUSED_CHARS = frozenset(";&<>(){}`$\\!")
#: How long a call waits for a `starting` record's fork id, in seconds.
STARTING_WAIT_S = 1.0
#: How long a judge waits for the session's lock, in seconds.
LOCK_WAIT_S = 0.5
_POLL_S = 0.05


def guarded(env: Mapping[str, str]) -> bool:
    """Whether a session runs the recall-fork guards: its launch turned the subconscious
    on. `env` is the session's environment, read once at its start."""
    return mode(env) is not None


def _named(session_id: str, agent_id: str) -> tuple[dict | None, bool]:
    """(the record whose fork is `agent_id`, whether a record is still `starting`)."""
    starting = False
    for record in reversed(records.session_records(session_id)):
        if record.get("fork") == agent_id:
            return record, False
        if record.get("state") == records.STARTING and not record.get("fork"):
            starting = True
    return None, starting


def fork_record(session_id: str | None, agent_id: str | None, *,
                wait_s: float = STARTING_WAIT_S) -> dict | None:
    """The recall record whose fork made a call from `agent_id`, or None when the call
    is not a recall fork's. See the module's docstring."""
    if not agent_id or not recalls.is_safe(session_id):
        return None
    record, starting = _named(session_id, agent_id)
    if record is not None or not starting:
        return record
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(_POLL_S)
        record, starting = _named(session_id, agent_id)
        if record is not None or not starting:
            return record
    for found in reversed(records.session_records(session_id)):
        if found.get("state") == records.STARTING and not found.get("fork"):
            return found
    return None


def time_up(record: dict, *, now: float | None = None) -> bool:
    """Whether the recall's fork is refused every further call."""
    now = time.time() if now is None else now
    settings = record.get("settings") or {}
    if record.get("state") in (records.HURRIED, records.LOST):
        return True
    try:
        if now - float(record["at"]) >= float(settings["limit_s"]):
            return True
        return int(record.get("tools") or 0) >= int(settings["max_tools"])
    except (KeyError, TypeError, ValueError):
        return True


def plain_read(command: object, fork_commands) -> bool:
    """Whether shell text `command` is a plain read of the listed commands."""
    if not isinstance(command, str) or not command.strip():
        return False
    if not command.isprintable() or any(c in REFUSED_CHARS for c in command):
        return False
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|")
    lexer.whitespace_split = True
    lexer.commenters = ""  # a `#` hides nothing from the test
    try:
        words = list(lexer)
    except ValueError:
        return False
    entries = [e.split() for e in fork_commands or () if isinstance(e, str) and e.split()]
    commands: list[list[str]] = [[]]
    for word in words:
        if word and set(word) == {"|"}:
            if word != "|":
                return False
            commands.append([])
        else:
            commands[-1].append(word)
    for argv in commands:
        if not argv or "=" in argv[0]:
            return False
        if not any(argv[:len(entry)] == entry for entry in entries):
            return False
    return True


def allowed(call, settings: Mapping[str, object]) -> bool:
    """Whether a fork's call is one it may make: a read tool, or a plain read in the
    shell, in the foreground, under the harness's own timeout."""
    if call.tool in READ_TOOLS:
        return True
    if call.tool != SHELL:
        return False
    given = call.input or {}
    if given.get("run_in_background") or given.get("timeout") is not None:
        return False
    return plain_read(given.get("command"), settings.get("fork_commands"))


def judge_call(call):
    """The call-step judge of `memory.subconscious-fork`."""
    from moxi_turns import Deny

    record = fork_record(call.session_id, call.agent_id)
    if record is None:
        return None
    session_id, recall_id = str(call.session_id), str(record["recall"])
    with recalls.session_lock(session_id, timeout_s=LOCK_WAIT_S):
        record = records.read(session_id, recall_id) or record
        if time_up(record):
            return Deny(TIME_UP)
        if not allowed(call, record.get("settings") or {}):
            return Deny(READ_ONLY)
        records.update(session_id, recall_id, tools=int(record.get("tools") or 0) + 1)
    return None


def judge_ask(call):
    """The ask-step judge of `memory.subconscious-fork-ask`."""
    from moxi_turns import Deny

    if fork_record(call.session_id, call.agent_id) is None:
        return None
    return Deny(ASK_REFUSED)


__all__ = ["TIME_UP", "READ_ONLY", "ASK_REFUSED", "READ_TOOLS", "SHELL", "REFUSED_CHARS",
           "STARTING_WAIT_S", "LOCK_WAIT_S", "guarded", "fork_record",
           "time_up", "plain_read", "allowed", "judge_call", "judge_ask"]

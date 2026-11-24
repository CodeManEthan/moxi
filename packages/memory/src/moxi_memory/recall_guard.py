"""The judges of memory's recall-fork guards (`moxi_memory.guards`): a recall's fork may
read and answer, and nothing else, and asks no one.

**Knowing its fork.** A subagent's call is a recall fork's when its agent id is the
`fork` of a recall record of the session, in any state (`fork_record`). From the moment
a recall's record is written until its child writes the fork's id, the record awaits
its fork (`awaits_fork`), whether it is `starting` or `hurried`: a newer recall can hurry
it after its first round was asked and before the fork's id is written, and that
changes what the fork may still do, not whether its calls are judged. While the session
has a record that awaits its fork, a call from an agent id no record names waits up to
`STARTING_WAIT_S` for the id, and is then judged as the fork of the newest such record
whose first round was asked (the record's `rounds`, written before the round goes to
turns), or of the newest such record when none was asked: only an asked round can have
started a fork. A record whose child is gone (`moxi.proc.alive` is false for the child
its log line names) will never start a fork, so it counts as no fork. A record with no
line yet can't be told apart from a child still starting up, since only the line names
the child, so it still counts. Every other subagent's call is left alone.

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
  is refused with `READ_ONLY`. That includes Claude Code's other shell tools: its
  `PowerShell` tool (on Windows) parses a command by other rules than the POSIX ones
  the plain-read test knows, so a command the test reads as one plain read could run
  more there, and its `Monitor` tool runs a command in the background.

Each call allowed is counted on the record, under the session's lock. A judge has
`BUDGET_S` from its start, inside its guard's deadline: the wait for a fork's
id and the wait for the lock both come out of it. When the lock is still held
(a child acks a round under it) once the budget is spent, the judge decides from the
record as it reads without the lock (records are written whole): the call is refused
as above, or allowed without being counted, so a busy lock never breaks the guard.

**A plain read (`plain_read`).** The test allows what it can see whole and refuses the
rest: every character prints (`str.isprintable`) and none is one of `REFUSED_CHARS`; the
text splits into words as a POSIX shell splits it with those characters gone (`_words`):
a space ends a word outside quotes, single and double quotes hold their text as it is
and are dropped, an unclosed quote is refused, and `#` is a plain character, so a
comment hides no command from the test; a `|` outside quotes is a pipe and one inside
quotes is a character of its word, as it is to the shell; no command of the pipeline is
empty (so `||` is refused); and in each command the first word holds no `=` and the
leading words match an entry of the list word for word.

**A fork's permission ask (`judge_ask`).** Refused with `ASK_REFUSED`, so the person
never sees a dialog for it; every other subagent's ask is left to the person.

**Where they run (`guarded`).** Only in a session whose launch turned the subconscious
on, decided once at the session's start.

Standard library at import; the core is imported at use.
"""

from __future__ import annotations

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
#: How long a call waits for the fork id of a record that awaits it, in seconds.
STARTING_WAIT_S = 1.0
#: How long a judge may take, from its start, in seconds: under the guards' deadline
#: (`moxi_memory.guards`), with room for turns' own wait for a thread.
BUDGET_S = 1.6
_POLL_S = 0.05


def guarded(env: Mapping[str, str]) -> bool:
    """Whether a session runs the recall-fork guards: its launch turned the subconscious
    on. `env` is the session's environment, read once at its start."""
    return mode(env) is not None


def child_gone(session_id: str, record: Mapping[str, object]) -> bool:
    """Whether the child of recall `record` is known to have ended: its log line names a
    child, and `moxi.proc.alive` is false for it. False with no line, or a line that
    names no child, since then nothing tells."""
    import moxi.proc

    line = recalls.find_line(session_id, str(record.get("recall")))
    child = line.get("child") if line is not None else None
    if not isinstance(child, dict):
        return False
    try:
        proc = moxi.proc.ProcId(int(child["pid"]), float(child["started"]))
    except (TypeError, KeyError, ValueError):
        return False
    return not moxi.proc.alive(proc)


def awaits_fork(session_id: str, record: Mapping[str, object]) -> bool:
    """Whether `record` is a recall whose fork's id is not yet written while its child
    may still write it: `starting` or `hurried`, no fork, and its child not known gone."""
    return (record.get("state") in (records.STARTING, records.HURRIED)
            and not record.get("fork") and not child_gone(session_id, record))


def _asked(record: Mapping[str, object]) -> bool:
    """Whether a round of `record`'s recall was asked, so its fork may have started."""
    try:
        return int(record.get("rounds") or 0) > 0
    except (TypeError, ValueError):
        return False


def _named(session_id: str, agent_id: str) -> tuple[dict | None, bool]:
    """(the record whose fork is `agent_id`, whether a record awaits its fork)."""
    awaiting = False
    for record in reversed(records.session_records(session_id)):
        if record.get("fork") == agent_id:
            return record, False
        if not awaiting and awaits_fork(session_id, record):
            awaiting = True
    return None, awaiting


def fork_record(session_id: str | None, agent_id: str | None, *,
                wait_s: float = STARTING_WAIT_S) -> dict | None:
    """The recall record whose fork made a call from `agent_id`, or None when the call
    is not a recall fork's. See the module's docstring."""
    if not agent_id or not recalls.is_safe(session_id):
        return None
    record, awaiting = _named(session_id, agent_id)
    if record is not None or not awaiting:
        return record
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(_POLL_S)
        record, awaiting = _named(session_id, agent_id)
        if record is not None or not awaiting:
            return record
    found = [r for r in reversed(records.session_records(session_id))
             if awaits_fork(session_id, r)]
    asked = [r for r in found if _asked(r)]
    return (asked or found or [None])[0]


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


#: The pipe among `_words`' words.
_PIPE = None


def _words(command: str) -> list[str | None] | None:
    """`command`'s words as a POSIX shell splits text that holds none of
    `REFUSED_CHARS`, with `_PIPE` for each `|` outside quotes; None for an unclosed
    quote."""
    words: list[str | None] = []
    word: list[str] = []
    in_word = False
    quote = None
    for char in command:
        if quote is not None:
            if char == quote:
                quote = None
            else:
                word.append(char)
        elif char in "'\"":
            quote, in_word = char, True
        elif char == " " or char == "|":
            if in_word:
                words.append("".join(word))
                word, in_word = [], False
            if char == "|":
                words.append(_PIPE)
        else:
            word.append(char)
            in_word = True
    if quote is not None:
        return None
    if in_word:
        words.append("".join(word))
    return words


def plain_read(command: object, fork_commands) -> bool:
    """Whether shell text `command` is a plain read of the listed commands."""
    if not isinstance(command, str) or not command.strip():
        return False
    if not command.isprintable() or any(c in REFUSED_CHARS for c in command):
        return False
    words = _words(command)
    if words is None:
        return False
    entries = [e.split() for e in fork_commands or () if isinstance(e, str) and e.split()]
    commands: list[list[str]] = [[]]
    for word in words:
        if word is _PIPE:
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


def _refusal(call, record: dict) -> str | None:
    if time_up(record):
        return TIME_UP
    if not allowed(call, record.get("settings") or {}):
        return READ_ONLY
    return None


def judge_call(call):
    """The call-step judge of `memory.subconscious-fork`."""
    from moxi.files import LockTimeout
    from moxi_turns import Deny

    started = time.monotonic()
    record = fork_record(call.session_id, call.agent_id,
                         wait_s=min(STARTING_WAIT_S, BUDGET_S))
    if record is None:
        return None
    session_id, recall_id = str(call.session_id), str(record["recall"])
    left = max(BUDGET_S - (time.monotonic() - started), 0.0)
    try:
        with recalls.session_lock(session_id, timeout_s=left):
            record = records.read(session_id, recall_id) or record
            refusal = _refusal(call, record)
            if refusal is not None:
                return Deny(refusal)
            records.update(session_id, recall_id, tools=int(record.get("tools") or 0) + 1)
    except LockTimeout:
        refusal = _refusal(call, records.read(session_id, recall_id) or record)
        return Deny(refusal) if refusal is not None else None
    return None


def judge_ask(call):
    """The ask-step judge of `memory.subconscious-fork-ask`."""
    from moxi_turns import Deny

    if fork_record(call.session_id, call.agent_id) is None:
        return None
    return Deny(ASK_REFUSED)


__all__ = ["TIME_UP", "READ_ONLY", "ASK_REFUSED", "READ_TOOLS", "SHELL", "REFUSED_CHARS",
           "STARTING_WAIT_S", "BUDGET_S", "guarded", "child_gone", "awaits_fork",
           "fork_record",
           "time_up", "plain_read", "allowed", "judge_call", "judge_ask"]

"""The judges of memory's recall-fork guards (`moxi_memory.guards`): a recall's fork may
read and answer, and nothing else, and asks no one.

**Knowing its fork.** A subagent's call is a recall fork's when its agent id is the
`fork` of a recall record of the session, in any state (`fork_record`). From the moment
a recall's record is written until its child writes the fork's id, the record may await
its fork (`awaits_fork`), whether it is `starting` or `hurried`: a newer recall can hurry
it after its first round was asked and before the fork's id is written, and that
changes what the fork may still do, not whether its calls are judged. While the session
has a record that awaits its fork, a call from an agent id no record names waits up to
`STARTING_WAIT_S` for the id, and is then judged as the fork of the newest such record
whose first round was asked (the record's `rounds`, written before the round goes to
turns), or of the newest such record when none was asked: only an asked round can have
started a fork. Newest is by the records' write sequence (`records.order`), never by
their `at`, which a clamp moves.

A record awaits its fork only while its holder can still start one. Its holder is the
process the record names (`moxi_memory.recall_holder`): the starter, then the child, from
the record's first write; for a record written before records named one, the child its
log line names. A record whose holder is gone (`recall_holder.alive` is false) will
never start a fork, so it counts as no fork. One whose holder lives awaits its fork at
most `holder_wait_s` from the recall's start (`subconscious.limit_s` plus the round's
margin plus `records.STARTING_GRACE_S`, 120 s at the defaults), by when the first
round's bound has passed and its child has read the fork's start; past it the record
counts as no fork, and memory's doctor lists it. A record that names no holder (its
holder's identity could not be read) can't be told apart from a child still starting
up: it awaits its fork only until `records.STARTING_GRACE_S` from its start. Ages are
measured by `recall_holder.age`, on a clock no wall-clock step moves where the system
has one. `waiting` reads the holder of a record on any route, as memory's doctor lists
it; only records of the subagent route await a fork here (`awaits_fork`): a fork on
another route is not a subagent of the session. Every other subagent's call is left
alone.

**A slot fork** is a session of its own, stamped `SUBCONSCIOUS_FORK_ENV` with the id of
the recall it serves (`slot_fork`). Every call there is the fork's, judged against that
recall's record, found in whichever session's directory holds it (`slot_record`), by
the same rules and under the same lock and count as an in-process fork's; a fork whose
record can't be found is refused every call. Its every permission ask is refused with no
judge (`moxi_memory.guards`).

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
- A Codex slot fork's calls reach the judge under the names Codex's hooks give them:
  its shell is `Bash`, with the command text under `command`, judged as above; a
  `command` that is not text (an argv) is refused, since the hook hands only text and
  the test judges only what it can see whole. It has no read tools, so it reads through
  the shell alone; `apply_patch`, `spawn_agent`, `write_stdin` and MCP tools are refused
  like any other call. The hook doesn't hand the guard the shell's `yield_time_ms`, so a
  plain read that doesn't end (`tail -f`) yields and goes on running in the background,
  where the fork can't read it (`write_stdin` is refused), until the slot fork exits
  with its recall.

Each call allowed is counted on the record, under the session's lock. A judge has
`BUDGET_S` from its start, inside its guard's deadline: the wait for a fork's
id and the wait for the lock both come out of it. When the lock is still held
(a child acks a round under it) once the budget is spent, the judge decides from the
record as it reads without the lock (records are written whole): the call is refused
as above, or allowed without being counted, so a busy lock never breaks the guard.

**A plain read (`plain_read`).** The test allows what it can see whole and refuses the
rest: every character prints (`str.isprintable`) and none is one of `REFUSED_CHARS`;
turns' shell reader (`read_shell`) then reads the text, and every mark it gives refuses
the call but a glob, a tilde and a `|` outside quotes, the one operator. So a space ends
a word outside quotes, single and double quotes hold their text as it is and are
dropped, an unclosed quote is refused, a `#` that starts a word is a comment and is
refused, and a `|` inside quotes is a character of its word, as it is to the shell; no
command of the pipeline is empty (so `||` and a trailing `|` are refused); and in each
command the first word holds no `=` and the leading words match an entry of the list
word for word.

**A fork's permission ask (`judge_ask`).** Refused with `ASK_REFUSED`, so the person
never sees a dialog for it; every other subagent's ask is left to the person.

**Where they run (`guarded`).** Only in a session whose launch turned the subconscious
on and picked the subagent route (`recall_start.route_stamp`, the reading
`recall_start.route` makes), decided once at the session's start, so a session on
another route never has a subagent's call judged as a recall fork's. A route stamp that
names no route runs no recall (`recall_start.UNKNOWN_ROUTE`) and no guard.

Standard library at import; the core is imported at use.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from . import recall_holder, recalls, records
from .recall_start import SUBAGENT, SUBCONSCIOUS_FORK_ENV, is_fork, mode, route_stamp

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
    on, on the subagent route. `env` is the session's environment, read once at its
    start."""
    return mode(env) is not None and route_stamp(env) == SUBAGENT


def holder_wait_s(record: Mapping[str, object]) -> float:
    """How long from its recall's start a record whose holder lives awaits its fork."""
    from .recall_stream import ROUND_MARGIN_S

    try:
        limit = float((record.get("settings") or {})["limit_s"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        limit = 60.0
    return limit + ROUND_MARGIN_S + records.STARTING_GRACE_S


def line_child(line: Mapping[str, object] | None):
    """The child (`moxi.proc.ProcId`) recall line `line` names, or None when there is no
    line or it names none (`child` null, as when `moxi.proc.identity` found no start
    time, or not a pid and a start time)."""
    import moxi.proc

    child = line.get("child") if line is not None else None
    if not isinstance(child, dict):
        return None
    try:
        return moxi.proc.ProcId(int(child["pid"]), float(child["started"]))
    except (TypeError, KeyError, ValueError):
        return None


#: Why a record that may await its fork does not (`waiting`).
HOLDER_GONE = "holder gone"
PAST_WAIT = "holder past its wait"
NO_HOLDER = "no holder"


def holder_of(session_id: str, record: Mapping[str, object]):
    """The holder `record` names, as a mapping, else the child its log line names (a
    record written before records named one, or one whose holder's identity could not
    be read); None when neither names one."""
    value = record.get("holder")
    if recall_holder.named(value):
        return value
    child = line_child(recalls.find_line(session_id, str(record.get("recall"))))
    return {"pid": child.pid, "started": child.started} if child is not None else None


def waiting(session_id: str, record: Mapping[str, object], *, now: float | None = None,
            boot: dict | None = None) -> tuple[bool, str | None]:
    """(whether `record`, on any route, still awaits its fork's id, why not when it might
    have: `HOLDER_GONE`, `PAST_WAIT` or `NO_HOLDER`). See the module's docstring."""
    if record.get("state") not in (records.STARTING, records.HURRIED) or record.get("fork"):
        return False, None
    age = recall_holder.age(record, now=now, boot=boot)
    held = holder_of(session_id, record)
    if held is not None:
        if not recall_holder.alive(held):
            return False, HOLDER_GONE
        if age >= holder_wait_s(record):
            return False, PAST_WAIT
        return True, None
    if age >= records.STARTING_GRACE_S:
        return False, NO_HOLDER
    return True, None


def awaits_fork(session_id: str, record: Mapping[str, object], *,
                now: float | None = None) -> bool:
    """Whether `record` is a subagent-route recall whose fork's id is not yet written
    while its holder may still write it. See the module's docstring."""
    if record.get("route") not in (None, SUBAGENT):
        return False
    return waiting(session_id, record, now=now)[0]


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


#: The marks of turns' shell reader a plain read may hold, once `REFUSED_CHARS` are gone:
#: a glob and a tilde, which the shell expands to names only. A `|` is the one operator.
_PLAIN_MARKS = frozenset({"glob", "tilde"})


def plain_read(command: object, fork_commands) -> bool:
    """Whether shell text `command` is a plain read of the listed commands."""
    if not isinstance(command, str) or not command.strip():
        return False
    if not command.isprintable() or any(c in REFUSED_CHARS for c in command):
        return False
    from moxi_turns import read_shell

    read = read_shell(command)
    for mark in read.marks:
        if mark.kind in _PLAIN_MARKS:
            continue
        if mark.kind == "operator" and command[mark.position] == "|" \
                and command[mark.position + 1:mark.position + 2] != "|":
            continue
        return False
    entries = [e.split() for e in fork_commands or () if isinstance(e, str) and e.split()]
    for one in read.commands:
        argv = [w.text for w in one.words]
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
    started = time.monotonic()
    record = fork_record(call.session_id, call.agent_id,
                         wait_s=min(STARTING_WAIT_S, BUDGET_S))
    if record is None:
        return None
    return _judge(call, str(call.session_id), record, started)


def slot_fork(env: Mapping[str, str]) -> bool:
    """Whether a session runs the slot guards: it is a recall's slot fork, stamped
    `SUBCONSCIOUS_FORK_ENV`. `env` is the session's environment, read once at its
    start."""
    return is_fork(env)


def slot_record(call) -> dict | None:
    """The record of the recall a slot fork's call serves, by the recall id its
    session's stamp names, or None when no record is found."""
    recall_id = (call.env or {}).get(SUBCONSCIOUS_FORK_ENV)
    return records.find(str(recall_id)) if recall_id else None


def judge_slot_call(call):
    """The call-step judge of `memory.subconscious-slot`: every call of a slot fork is
    the fork's, judged against the record its stamp names. A fork whose record can't be
    found is refused every call, as one whose time is up."""
    from moxi_turns import Deny

    started = time.monotonic()
    record = slot_record(call)
    if record is None or not recalls.is_safe(record.get("session")):
        return Deny(TIME_UP)
    return _judge(call, str(record["session"]), record, started)


def _judge(call, session_id: str, record: dict, started: float):
    """Judge a fork's `call` against `record`, of session `session_id`, under the
    session's lock, counting an allowed call on the record."""
    from moxi.files import LockTimeout
    from moxi_turns import Deny

    recall_id = str(record["recall"])
    left = max(BUDGET_S - (time.monotonic() - started), 0.0)
    try:
        with recalls.session_lock(session_id, timeout_s=left):
            record = records.clamp(session_id, recall_id) or record
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
           "STARTING_WAIT_S", "BUDGET_S", "guarded", "holder_wait_s", "line_child",
           "HOLDER_GONE", "PAST_WAIT", "NO_HOLDER", "holder_of", "waiting", "awaits_fork",
           "fork_record",
           "time_up", "plain_read", "allowed", "judge_call", "judge_ask", "slot_fork",
           "slot_record", "judge_slot_call"]

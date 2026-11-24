"""Starting a recall: the session's mode, which prompts count, whether a route can run,
and the record and detached child of a new recall.

**The mode.** A launch that turns the subconscious on stamps `SUBCONSCIOUS_ENV` with
its mode, `automatic` or `on-demand`; a session with no stamp, or with any other value,
has it off.

**Which prompts count (`counts`).** Config `subconscious.trigger` says which prompts are
new work: `user`, a prompt whose origin turns marks as the person's own (`own`);
`user-and-comms`, those and every prompt comms handed in (`delivery_by` is `comms`). A
prompt with no origin and no `delivery_by` (a launch's brief, the harness's notice of a
finished fork) never counts. In automatic mode a counted prompt starts a recall; in
on-demand mode it resets the agent's count of recalls (`reset_asked`).

**The route (`route`).** Every recall runs on Claude Code's subagent route, through
turns' `fork`. It is checked at every try, not once: a Claude Code session whose turns
endpoint offers `fork` has it; a Codex session has no route, nor does any other session
whose endpoint offers no `fork`. A try that finds no route writes the session's `off`
line with the reason (`recalls.note_off`), at the first such try and again only when the
reason changes. A route whose answers come back as a comms message (`BY_COMMS`) runs
only while the session's recall sender can send (`comms_why`,
`moxi_memory.recall_send`); its reason otherwise names the sender when comms knows it.

**A new recall (`begin`).** Under the session's subconscious lock it sets every recall of
the session still running `hurried` and writes the new recall's record, state
`starting`, holding everything its child runs on: the prompt's text or the topic, its
senders and the subconscious settings the caller holds. Then it starts the child,
`moxi-memory-subconscious`, with the session and the recall's id, through the detached
starter (`spawn_starter`), and returns without waiting for it: the starter starts the
child detached and exits, so the child runs outside every session. A child that can't
be started, by this process or by the starter, leaves the record and a log line `lost`,
with the reason `spawn`.

**The agent's count (`asked`).** In on-demand mode the agent may start at most
`subconscious.on_demand_per_prompt` recalls between two counted prompts. The count lives
at `subconscious/asked/<session>.json`, read and raised by the command and reset by the
listener, each under the session's lock.

**On demand (`ask`).** `moxi memory subconscious recall TOPIC` finds its session by its
own process id through turns' client, reads the session's mode from its stamps, reads
memory's settings itself and passes them on, checks the route, raises the agent's
count, and begins a recall with the topic in place of a prompt (`start`: `asked`). It
refuses a session where the subconscious is off (with the reason) or automatic, a count
at its cap, and a config file in error (with the error; the command keeps no last good
settings). The start line (`START_LINE`) tells an on-demand session's agent the command
exists.

Standard library at import; the core and turns are imported at use.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping

from . import recalls, records

#: The variable a launch stamps with the session's subconscious mode.
SUBCONSCIOUS_ENV = "MOXI_MEMORY_SUBCONSCIOUS"
#: The modes.
AUTOMATIC = "automatic"
ON_DEMAND = "on-demand"
MODES = (AUTOMATIC, ON_DEMAND)
#: The one route a recall runs on.
SUBAGENT = "subagent"
#: The routes whose answers come back to the session as a comms message.
BY_COMMS: tuple[str, ...] = ()
#: Why a session's subconscious can't run.
NO_FORK = "no turns fork in this session"
CODEX = "Codex: no recall route runs on Codex"
#: The values of `subconscious.trigger`.
USER = "user"
USER_AND_COMMS = "user-and-comms"
#: The child's console script.
SCRIPT = "moxi-memory-subconscious"
#: The agent's counts' directory under the subconscious's directory.
ASKED = "asked"
#: The reason on a recall whose child could not be started.
SPAWN = "spawn"


def mode(env: Mapping[str, str] | None) -> str | None:
    """The session's subconscious mode from its environment, or None when it is off."""
    value = (env or {}).get(SUBCONSCIOUS_ENV)
    return value if value in MODES else None


def counts(event, trigger: str) -> bool:
    """Whether prompt `event` is new work under `subconscious.trigger` value `trigger`."""
    if getattr(event, "own", None) is True:
        return True
    return trigger == USER_AND_COMMS and getattr(event, "delivery_by", None) == "comms"


def route(harness: str | None, session_id: str) -> tuple[str | None, str | None]:
    """(the route a recall of the session runs on, None), or (None, why none can run)."""
    if harness == "codex":
        return None, CODEX
    if harness != "claude":
        return None, NO_FORK
    try:
        import moxi_turns as client

        found = client.find(session_id=session_id)
        if found is None:
            return None, NO_FORK
        with found:
            offered = found.capabilities
    except Exception:  # noqa: BLE001 - an endpoint that can't be asked offers no fork
        return None, NO_FORK
    if "fork" not in offered:
        return None, NO_FORK
    if SUBAGENT in BY_COMMS:
        why = comms_why(session_id)
        if why is not None:
            return None, why
    return SUBAGENT, None


def comms_why(session_id: str) -> str | None:
    """Why the session's answers can't go back by comms now (one of
    `recall_send.REASONS`, followed by the recall sender's name when comms knows it),
    or None when they can."""
    from . import recall_send

    found = recall_send.way_back(session_id)
    if found.ok:
        return None
    return f"{found.why}: {found.name}" if found.name else found.why


def subconscious_settings(settings) -> dict:
    """Every `subconscious.*` value of `settings`, keyed by its last name."""
    prefix = "subconscious."
    return {key[len(prefix):]: settings[key] for key in settings_keys()
            if key.startswith(prefix)}


def settings_keys() -> list[str]:
    """The keys of memory's schema."""
    from .settings import schema

    return list(schema().keys)


def default_settings() -> dict:
    """Every `subconscious.*` key's default, keyed by its last name."""
    from .settings import schema

    prefix = "subconscious."
    return {key[len(prefix):]: spec.default for key, spec in schema().keys.items()
            if key.startswith(prefix)}


def new_id() -> str:
    """A new recall id."""
    return uuid.uuid4().hex[:16]


def begin(session_id: str, harness: str | None, *, start: str, text: str,
          senders, settings: Mapping[str, object], cwd: str | None,
          notes_dir: str | None, route_name: str, recall_senders=(),
          now: float | None = None, spawn=None) -> dict:
    """Write a new recall's record, hurrying the session's running one, start its child
    and return the record. See the module's docstring."""
    import time

    now = time.time() if now is None else now
    recall_id = new_id()
    with recalls.session_lock(session_id):
        records.hurry_running(session_id, now=now)
        record = records.write({
            "recall": recall_id, "session": session_id, "harness": harness,
            "state": records.STARTING, "start": start, "text": text,
            "senders": list(senders or ()), "recall_senders": list(recall_senders or ()),
            "settings": dict(settings), "cwd": cwd,
            "notes_dir": notes_dir, "route": route_name, "tools": 0, "answered": False,
            "rounds": 0}, now=now)
    try:
        (spawn or spawn_child)(session_id, recall_id)
    except Exception as exc:  # noqa: BLE001 - a child that can't start loses the recall
        record = lose_spawn(session_id, recall_id, f"{type(exc).__name__}: {exc}") or record
    return record


def lose_spawn(session_id: str, recall_id: str, error: str) -> dict | None:
    """End recall `recall_id`, whose child could not be started, `lost` with the reason
    `spawn`, record and line together, under the session's lock; the record as written,
    or None when it has none."""
    with recalls.session_lock(session_id):
        record = records.update(session_id, recall_id, state=records.LOST, reason=SPAWN,
                                error=error)
        known = record or {}
        recalls.put_line(session_id, {
            "recall": recall_id, "harness": known.get("harness"), "state": recalls.LOST,
            "reason": SPAWN, "error": error, "start": known.get("start"),
            "head": recalls.head(known.get("text") or ""), "route": known.get("route")})
    return record


#: The flag that runs `moxi-memory-subconscious` as the detached starter.
DETACH = "--detach"


def spawn_starter(args: list[str]) -> int:
    """Start `moxi-memory-subconscious --detach ARGS`, detached, its output discarded,
    and return its process id without waiting for it. The starter starts
    `moxi-memory-subconscious ARGS` detached in its turn and exits, so that process's
    parent is the system's and it runs outside every session: comms takes a send under
    a scoped sender only from such a process."""
    import moxi.paths
    import moxi.proc

    return moxi.proc.spawn([str(moxi.paths.script(SCRIPT)), DETACH, *args],
                           detached=True, output=os.devnull, cwd=os.sep)


def spawn_child(session_id: str, recall_id: str) -> int:
    """Start the recall's child through the detached starter (`spawn_starter`), and
    return the starter's process id without waiting for it."""
    return spawn_starter(["--session", session_id, "--recall", recall_id])


# -- the on-demand command -------------------------------------------------------------

#: What the on-demand command prints when it started a recall.
STARTED = ('Started a recall on "{topic}". Its points come back to you as it finds them; '
           "keep working.")
#: The line memory's `start` listener adds in an on-demand session.
START_LINE = ('subconscious: `moxi memory subconscious recall "<topic>"` starts a second '
              "agent that searches past sessions and notes for the topic and sends you "
              "what it finds. Use it when a topic has much prior work you don't have in "
              "front of you; each recall spends tokens.")


class Refused(Exception):
    """An on-demand recall the command refuses, with the reason."""


def this_session():
    """The session the calling process runs in, found by its own process id through
    turns' client: (session id, harness, the session's environment stamps, its launch
    directory), or None when no turns endpoint serves it."""
    import moxi_turns as client

    found = client.find(pid=os.getpid())
    if found is None:
        return None
    with found:
        try:
            env = {k: v for k, v in found.env([SUBCONSCIOUS_ENV]).items() if v is not None}
        except Exception:  # noqa: BLE001 - an endpoint that can't read it: the caller's own
            value = os.environ.get(SUBCONSCIOUS_ENV)
            env = {SUBCONSCIOUS_ENV: value} if value is not None else {}
        return found.session_id, found.harness, env, found.info.get("cwd")


def ask(topic: str, *, session=None, spawn=None) -> dict:
    """Start an on-demand recall on `topic` for the calling session and return its
    record. Raises `Refused`, with the line to print, for a session where the
    subconscious is off or automatic, a cap used up, or memory's config file in error.
    `session` stands in for `this_session()`'s answer."""
    from moxi.config import ConfigError

    from . import settings

    topic = " ".join(str(topic).split())
    if not topic:
        raise Refused("give the topic to recall")
    found = this_session() if session is None else session
    if found is None or not recalls.is_safe(found[0]):
        raise Refused("this shell runs in no session turns serves, so no recall can start")
    session_id, harness, env, cwd = found
    run_mode = mode(env)
    if run_mode is None:
        raise Refused("the subconscious is off in this session")
    if run_mode == AUTOMATIC:
        raise Refused("this session runs the subconscious automatically: every prompt "
                      "starts a recall, so none is started on demand")
    try:
        held = subconscious_settings(settings.read())
    except ConfigError as exc:
        raise Refused(f"memory's config file is in error, so no recall starts: {exc}") \
            from None
    route_name, why = route(harness, session_id)
    if route_name is None:
        recalls.note_off(session_id, why, harness=harness)
        raise Refused(f"the subconscious is off in this session: {why}")
    cap = int(held["on_demand_per_prompt"])
    with recalls.session_lock(session_id):
        count = asked(session_id)
        if count >= cap:
            raise Refused(f"this prompt's recalls are used up ({cap}); the next prompt "
                          "of new work allows more")
        write_asked(session_id, count + 1)
    from .listener import notes_override

    return begin(session_id, harness, start=recalls.ASKED, text=topic, senders=(),
                 settings=held, cwd=cwd, notes_dir=notes_override(os.environ),
                 route_name=route_name, spawn=spawn)


# -- the agent's count, in on-demand mode -----------------------------------------------

def _asked_path(session_id: str):
    if not recalls.is_safe(session_id):
        raise ValueError(f"{session_id!r} is not a session id")
    return recalls.subconscious_dir() / ASKED / f"{session_id}.json"


def asked(session_id: str) -> int:
    """How many recalls the agent started since the last counted prompt."""
    try:
        value = json.loads(_asked_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    count = value.get("count") if isinstance(value, dict) else None
    return count if isinstance(count, int) and count >= 0 else 0


def write_asked(session_id: str, count: int) -> None:
    """Set the agent's count. The caller holds the session's lock."""
    from moxi import files

    path = _asked_path(session_id)
    files.private_dir(path.parent)
    files.atomic_write(path, json.dumps({"count": count}) + "\n")


def reset_asked(session_id: str) -> None:
    """Set the agent's count back to 0, under the session's lock."""
    with recalls.session_lock(session_id):
        if asked(session_id):
            write_asked(session_id, 0)


__all__ = ["SUBCONSCIOUS_ENV", "AUTOMATIC", "ON_DEMAND", "MODES", "SUBAGENT", "BY_COMMS",
           "NO_FORK", "CODEX", "USER", "USER_AND_COMMS", "SCRIPT", "ASKED", "SPAWN", "mode",
           "counts", "route", "comms_why", "subconscious_settings", "settings_keys",
           "default_settings", "new_id", "begin", "lose_spawn", "DETACH", "spawn_starter",
           "spawn_child", "STARTED", "START_LINE",
           "Refused", "this_session", "ask", "asked", "write_asked", "reset_asked"]

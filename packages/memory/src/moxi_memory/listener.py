"""The functions behind memory's turns listeners: the start line, the start lists, the
reminder and the ingest trigger.

turns imports this module in a listener thread of the session's endpoint, at a function's
first ask. It imports only the standard library at import time.

`start(event)` answers a `start` with the start line, the same for every harness. It names
the session's project label when the session's folder gives one, and leaves the
parenthesis about `--project` out when it doesn't. In a session whose launch turned the
subconscious on on demand, a second line names `moxi memory subconscious recall`.

`prompt(event)` answers a main-loop prompt with the reminder, the same for every harness,
or None when config `reminder.enabled` is off.

`fire(event)` only starts the ingest child for a `turn` or a `gone`, detached, and
returns (`moxi_memory.start.fire`).

`subconscious(event)` answers a main-loop prompt with nothing, always, and at once. In a
session whose launch turned the subconscious on (`moxi_memory.recall_start`), a prompt
config `subconscious.trigger` counts starts a recall in automatic mode, once a route can
run (else it logs the session's `off` line), and resets the agent's count of recalls in
on-demand mode. It runs on the last good `subconscious.*` settings while memory's config
file is in error, and passes them to the recall's child. It never raises.

`subconscious_gone(event)` answers a `gone` of a session whose launch turned the
subconscious on: when a recall's line of the session is still open or kept, it starts a
taker for the session's lines, detached (`moxi_memory.recall_take`), and returns. It
never raises.

`lists(event)` answers a `start` with two lists of recent sessions, read from memory's
session table and nothing else: this agent's own recent sessions from any project, then
the project's, each leaving out this session, subagents, scratch and fork launches and
sessions whose launch kind isn't settled. It works to one deadline, `DEADLINE` seconds after its
first line runs, and every wait in it has its own cap and is cut to the time left:

1. It writes this session's row (the harness, the id, and the label when the row has
   none), waiting at most `ROW_WAIT` for the store.
2. Then it asks comms for the session's identity, in a daemon thread of its own, and
   waits at most `IDENTITY_WAIT`. An identity in time is written to the row, waiting at
   most `IDENTITY_WRITE_WAIT`. One that comes later is written by that thread when it
   comes, only to this session's row, only if the row is there and still holds no
   identity, and never as an empty value; the next start uses it.
3. It runs the two list queries, each at most `QUERY_WAIT`. A query cut off leaves its
   list out.

A step that has no time left is skipped. No answer opens the index, and none of these
ever raises: a store that is busy or broken costs the lists, never the start line.
"""

from __future__ import annotations

import logging
import os
import shlex
import threading
import time

#: The start line, with `{project}` filled in by `PROJECT` or by nothing.
START_LINE = ("memory: `moxi memory search \"<words>\"` searches past sessions in every "
              "project{project}. `moxi sessions read <session>` shows part of one of the "
              "sessions listed below. Search when the task needs something from an earlier "
              "session.")
#: The start line's parenthesis, with the session's label.
PROJECT = " (`--project {label}` for this one)"
#: The reminder at each prompt.
REMINDER = "memory: search past sessions if this turn needs them."

#: The whole `lists` answer's deadline, in seconds from its first line.
DEADLINE = 1.5
#: The most the row write waits for the store.
ROW_WAIT = 0.25
#: The most `lists` waits for comms' answer about the identity.
IDENTITY_WAIT = 0.5
#: The most an identity write waits for the store.
IDENTITY_WRITE_WAIT = 0.25
#: The most each list query runs.
QUERY_WAIT = 0.25
#: How many sessions each list shows.
SHOWN = 5
#: The lists' headings.
AGENT_HEADING = "your recent sessions ({identity}):"
PROJECT_HEADING = "recent sessions in {label}:"


# -- the session's folder ---------------------------------------------------------------

def _work_dir(value) -> str | None:
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    if not os.path.isabs(value) or not os.path.isdir(value):
        return None
    return os.path.normpath(value)


def session_path(event) -> str:
    """The session's folder: `event.cwd`, then `PWD` from its environment, then this
    process's working directory."""
    env = getattr(event, "env", None) or {}
    return (_work_dir(getattr(event, "cwd", None)) or _work_dir(env.get("PWD"))
            or os.getcwd())


def _label(event) -> str | None:
    from .labels import project_label

    try:
        return project_label(session_path(event))
    except (OSError, ValueError):
        return None


# -- start, prompt, fire ----------------------------------------------------------------

def start_line(label: str | None) -> str:
    """The start line for a session whose project label is `label` (None: no label)."""
    project = PROJECT.format(label=shlex.quote(label)) if label else ""
    return START_LINE.format(project=project)


def start(event) -> str:
    """The start line for `event`'s session, and the on-demand command's line under it
    in a session whose launch turned the subconscious on on demand."""
    line = start_line(_label(event))
    from . import recall_start

    if recall_start.mode(getattr(event, "env", None)) == recall_start.ON_DEMAND:
        return f"{line}\n{recall_start.START_LINE}"
    return line


def prompt(event) -> str | None:
    """The reminder, or None when config `reminder.enabled` is off. A config memory can't read
    gives the default, which is on."""
    from moxi.config import ConfigError

    from . import settings

    try:
        on = bool(settings.read()["reminder.enabled"])
    except ConfigError:
        on = bool(settings.schema().keys["reminder.enabled"].default)
    return REMINDER if on else None


#: The last good `subconscious.*` settings this process read.
_good_subconscious: dict | None = None


def subconscious_settings() -> dict:
    """memory's `subconscious.*` settings now, keyed by their last names: the last good
    ones while the config file is in error, and the defaults when none were ever good."""
    global _good_subconscious
    from moxi.config import ConfigError

    from . import recall_start, settings

    try:
        _good_subconscious = recall_start.subconscious_settings(settings.read())
    except ConfigError:
        if _good_subconscious is None:
            return recall_start.default_settings()
    return dict(_good_subconscious)


def subconscious(event) -> None:
    """Start a recall for a counted prompt in an automatic session, or reset the agent's
    count in an on-demand one, and return None at once. See the module's docstring."""
    try:
        _subconscious(event)
    except Exception:  # noqa: BLE001 - the prompt never waits on a recall's start
        logging.getLogger(__name__).exception("the subconscious's start failed")
    return None


def _subconscious(event) -> None:
    from . import recall_start, recalls

    env = getattr(event, "env", None) or {}
    mode = recall_start.mode(env)
    session_id = getattr(event, "session_id", None)
    if mode is None or not recalls.is_safe(session_id):
        return
    held = subconscious_settings()
    if not recall_start.counts(event, held["trigger"]):
        return
    if mode == recall_start.ON_DEMAND:
        recall_start.reset_asked(session_id)
        return
    harness = getattr(event, "harness", None)
    route, why = recall_start.route(harness, session_id)
    if route is None:
        recalls.note_off(session_id, why, harness=harness)
        return
    recall_start.begin(session_id, harness, start=recalls.PROMPT,
                       text=getattr(event, "text", None) or "",
                       senders=getattr(event, "delivery_senders", None) or (),
                       settings=held, cwd=session_path(event),
                       notes_dir=notes_override(env), route_name=route)


def subconscious_gone(event) -> None:
    """When a session with the subconscious on is ending and a recall's line of it is
    still open or kept, start a taker for its lines, detached, and return None at once."""
    try:
        from . import recall_start, recall_take

        session_id = getattr(event, "session_id", None)
        if (recall_start.mode(getattr(event, "env", None)) is not None
                and recall_take.session_needs_taking(session_id)):
            recall_take.start_taker(session_id)
    except Exception:  # noqa: BLE001 - an ending session never waits on a taker's start
        logging.getLogger(__name__).exception("the subconscious's taker did not start")
    return None


def notes_override(env) -> str | None:
    """The session's notes directory override from its environment, when the notes
    module is installed and names one."""
    from . import links

    link = links.notes()
    if not link.ready:
        return None
    name = getattr(link.api, "NOTES_DIR_ENV", None)
    value = (env or {}).get(name) if isinstance(name, str) else None
    return value or None


def fire(event) -> None:
    """Start the ingest child for a `turn` or a `gone`, and return."""
    from . import start as starter

    starter.fire(event)
    return None


# -- lists ------------------------------------------------------------------------------

class _Clock:
    """The deadline of one `lists` answer."""

    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + seconds

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def cap(self, wait: float) -> float:
        """`wait`, cut to the time left; 0 or less means skip the step."""
        return min(wait, self.left())


def _open(seconds: float):
    """The state store, opened waiting at most `seconds`, or None."""
    from . import state

    if seconds <= 0:
        return None
    try:
        return state.connect(timeout=seconds)
    except Exception:  # noqa: BLE001 - a store that can't be opened costs only the lists
        return None


def _step(conn, seconds: float, fn, *args, **kwargs):
    """`fn(conn, *args, **kwargs)` within `seconds`: (True, its answer), or (False, None)
    when it ran out of time or failed."""
    from . import state

    if conn is None or seconds <= 0:
        return False, None
    try:
        with state.bounded(conn, seconds):
            return True, fn(conn, *args, **kwargs)
    except Exception:  # noqa: BLE001 - a step cut off or failing is skipped
        return False, None


class _Identity:
    """The ask for a session's identity, in a daemon thread. The answer is used when it
    comes in time; once `lists` has stopped waiting, the thread writes it itself."""

    def __init__(self, env, me: tuple[str, str]):
        self.env = dict(env or {})
        self.me = me
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.answer: str | None = None
        self.late = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="memory-identity", daemon=True).start()

    def _run(self) -> None:
        answer = None
        try:
            from . import links

            link = links.comms()
            if link.ready:
                answer = link.api.caller_identity(self.env)
        except Exception:  # noqa: BLE001 - an identity that can't be read is none
            answer = None
        if not (isinstance(answer, str) and answer.strip()):
            answer = None
        with self.lock:
            self.answer = answer
            late = self.late
            self.done.set()
        if late and answer is not None:
            write_late_identity(self.me, answer)

    def wait(self, seconds: float) -> str | None:
        """The answer if it comes within `seconds`; else None, and the thread writes it
        when it comes."""
        self.done.wait(max(0.0, seconds))
        with self.lock:
            if self.done.is_set():
                return self.answer
            self.late = True
            return None


def write_late_identity(me: tuple[str, str], identity: str) -> bool:
    """Write an identity that came after `lists` stopped waiting: only to this session's
    row, only when the row is there and holds no identity. Returns whether it wrote."""
    from . import table

    conn = _open(IDENTITY_WRITE_WAIT)
    if conn is None:
        return False
    try:
        ok, wrote = _step(conn, IDENTITY_WRITE_WAIT, table.write_late_identity, me[0],
                          me[1], identity)
        return bool(ok and wrote)
    finally:
        conn.close()


def _when(newest: float) -> str:
    from .clock import show

    return show(newest)


def format_lists(identity: str | None, agent_rows, label: str | None,
                 project_rows) -> str | None:
    """The two lists as text; a list with no rows is left out with its heading, and None
    when both are empty."""
    lines: list[str] = []
    if identity and agent_rows:
        lines.append(AGENT_HEADING.format(identity=identity))
        lines += [f"- {r.session_id}  {_when(r.newest)}" for r in agent_rows]
    if label and project_rows:
        lines.append(PROJECT_HEADING.format(label=label))
        lines += [f"- {r.session_id}  {_when(r.newest)}  {r.identity or r.harness}"
                  for r in project_rows]
    return "\n".join(lines) if lines else None


def lists(event) -> str | None:
    """The two start lists for `event`'s session, or None. See the module's docstring."""
    clock = _Clock(DEADLINE)
    harness = getattr(event, "harness", None)
    session_id = getattr(event, "session_id", None)
    if not (isinstance(harness, str) and harness and isinstance(session_id, str)
            and session_id):
        return None
    from . import table

    me = (harness, session_id)
    label = _label(event)
    conn = None
    try:
        # 1. this session's row, before anything is asked
        cap = clock.cap(ROW_WAIT)
        if cap > 0:
            ends = time.monotonic() + cap
            conn = _open(cap)
            _step(conn, ends - time.monotonic(), table.write_start, harness, session_id,
                  label)

        # 2. the identity
        identity = None
        cap = clock.cap(IDENTITY_WAIT)
        if cap > 0:
            asker = _Identity(getattr(event, "env", None), me)
            asker.start()
            identity = asker.wait(cap)
        if identity is not None:
            cap = clock.cap(IDENTITY_WRITE_WAIT)
            if cap > 0:
                ends = time.monotonic() + cap
                if conn is None:
                    conn = _open(cap)
                _step(conn, ends - time.monotonic(), table.write_identity, harness,
                      session_id, identity)

        # 3. the two lists
        agent_rows: list = []
        if identity is not None:
            cap = clock.cap(QUERY_WAIT)
            if cap > 0:
                ends = time.monotonic() + cap
                if conn is None:
                    conn = _open(cap)
                ok, found = _step(conn, ends - time.monotonic(), table.agent_sessions,
                                  identity, me, limit=SHOWN)
                agent_rows = list(found or ())[:SHOWN] if ok else []
        project_rows: list = []
        if label:
            cap = clock.cap(QUERY_WAIT)
            if cap > 0:
                ends = time.monotonic() + cap
                if conn is None:
                    conn = _open(cap)
                shown = [(r.harness, r.session_id) for r in agent_rows]
                ok, found = _step(conn, ends - time.monotonic(), table.project_sessions,
                                  label, me, leave_out=shown, limit=SHOWN)
                project_rows = list(found or ())[:SHOWN] if ok else []
        return format_lists(identity, agent_rows, label, project_rows)
    finally:
        if conn is not None:
            conn.close()


__all__ = ["START_LINE", "PROJECT", "REMINDER", "DEADLINE", "ROW_WAIT", "IDENTITY_WAIT",
           "IDENTITY_WRITE_WAIT", "QUERY_WAIT", "SHOWN", "session_path", "start_line",
           "start", "prompt", "fire", "subconscious", "subconscious_gone",
           "subconscious_settings",
           "notes_override", "lists", "format_lists", "write_late_identity"]

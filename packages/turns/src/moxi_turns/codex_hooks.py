"""Codex hook flags, their per-launch trust, and the host's ping server.

Hook commands carry their socket path on the command line because Codex runs them with
the session's saved environment. SessionStart and UserPromptSubmit return listener text
through ``moxi_turns.hooks``. Stop uses a two-phase block: the ping flushes the block to
stdout and acknowledges that write. No hook closes a turn: Stop and Interrupt are Codex
asking or telling, and the turn ends on Codex's own word (see ``codex``). A
Stop answered unblocked marks the turn ending, so it takes no more steers.

SessionStart fires inside a thread's first turn. Codex says ``resume`` there when the
thread was reloaded before it had any turn; while the host holds the thread unturned
(``start_source``), that ``resume`` is asked of the listeners as ``startup``, and once
the answer is out the host clears the mark (``start_answered``).

A subagent's hooks run turns' commands too. Its input carries the root thread's
``session_id`` and the subagent's own ``agent_id`` (absent, not null, on the main loop),
and the ping forwards both. A UserPromptSubmit that carries an ``agent_id`` gets no
listener text and raises no ``prompt`` event: those belong to the main loop's prompts.

The tool hooks. ``PreToolUse`` is in the launch's hook list only when a guard that is on
covers Codex, with a ``matcher`` built from every such guard's Codex patterns
(``matcher``), so Codex starts the ping only for those tools; ``PostToolUse`` only when an
installed listener row names ``after-tool``, for every tool. Both lists are fixed for the
launch: a guard turned on later covers Codex calls from the next launch on, and one
turned off stops at the next ``ready``. The ping forwards the tool's name, input and
id, the call's ``cwd`` and the ``transcript_path``; the host matches each call against
its table, runs the judges in its own process, and answers in Codex's hook output form:
a refusal as ``permissionDecision: "deny"`` with the reason, text for the model as
``additionalContext``. A tool hook is answered outside the lock the other hooks share,
so parallel subagents' calls are judged together.

A guard that declared ``on_error = "deny"`` holds when the host can't answer. The
``PreToolUse`` command carries ``--refuse-unanswered``, the cover rule (patterns, ``who``,
``match``) of every such guard at launch, fixed for the launch like the matcher. A ping
that gets no answer (no host, a host fault, or its wait passed) refuses the call when one
of those rows covers it, by the rule the host matches with, and prints nothing otherwise,
so the call runs. A call or a table it can't read refuses. A deny guard turned off later
still refuses through a dead host until the next launch, which fails closed.

Trust. Each hook is trusted for the launch by its hash in ``-c hooks.state={...}``: sha256
over the canonical JSON (sorted keys, no spaces) of ``{event_name, hooks: [handler]}``,
plus ``matcher`` for a group that has one. Its key is
``/<session-flags>/config.toml:<event_label>:<group>:<handler>``. One ``hooks.state``
holds every key, since a second replaces the first.
"""

from __future__ import annotations

import time

# A ping's wall clock at its start, before its other imports: it rides the ping's ask, and
# the host times the hook from it to its answer.
_STARTED = time.time()
# The imports below come after the stamp on purpose.

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from . import hooktimes, protocol
from .results import StopFacts

log = logging.getLogger("moxi.turns.codex.hooks")

KEY_SOURCE = "/<session-flags>/config.toml"
TRUSTED = "trusted"
DEFAULT_TIMEOUT = 600
INTERRUPT_TIMEOUT = 3
PING_TIMEOUT = 3.0
PRINTED = "printed"
#: The refusal of a call a deny guard covers when the host gave no answer; it names no
#: guard, since the ping can't tell which one would have broken.
CANNOT_CHECK = "turns could not check this call"


@dataclass(frozen=True)
class HookEvent:
    event: str
    label: str
    timeout: int
    source: str | None = None


EVENTS = (
    HookEvent("SessionStart", "session_start", DEFAULT_TIMEOUT),
    HookEvent("UserPromptSubmit", "user_prompt_submit", DEFAULT_TIMEOUT),
    HookEvent("Stop", "stop", DEFAULT_TIMEOUT),
    HookEvent("Interrupt", "interrupt", INTERRUPT_TIMEOUT),
    HookEvent("PostCompact", "post_compact", DEFAULT_TIMEOUT, "compact"),
)
EVENT_NAMES = tuple(event.event for event in EVENTS)
#: How long Codex lets a tool hook run, in seconds. The ping's own wait is shorter.
TOOL_TIMEOUT = 30
#: The ping's margin over the host's own bound on a tool hook's answer, in seconds.
TOOL_MARGIN_S = 1.0
PRE_TOOL = HookEvent("PreToolUse", "pre_tool_use", TOOL_TIMEOUT)
POST_TOOL = HookEvent("PostToolUse", "post_tool_use", TOOL_TIMEOUT)
#: The tool hooks, in the launch's hook list only when something needs them.
TOOL_EVENTS = (PRE_TOOL, POST_TOOL)
TOOL_EVENT_NAMES = tuple(event.event for event in TOOL_EVENTS)
#: Every event the ping answers.
PING_EVENTS = EVENT_NAMES + TOOL_EVENT_NAMES
#: The hook-time event each tool hook's wait is recorded under.
TIMED_AS = {"PreToolUse": "guard", "PostToolUse": "after-tool"}
#: The matcher that selects every tool.
EVERY_TOOL = "*"
#: A pattern of only these characters is a plain name, which Codex matches exactly.
_PLAIN = re.compile(r"[A-Za-z0-9_]+")
#: The characters a matcher regex escapes in a pattern's literal parts.
_REGEX_META = frozenset("\\.+*?()|[]{}^$")


class HooksUntrusted(RuntimeError):
    """The app-server did not report every launch hook as trusted."""


def hooks_socket(directory) -> Path:
    """The hooks' socket of the endpoint in `directory`: in it, or on the short path
    when that is too long for a socket address."""
    return Path(protocol.endpoint_socket(directory, protocol.CODEX_HOOKS_SOCK))


def command(ping: list[str], socket_path, event: HookEvent,
            wait_seconds: float = PING_TIMEOUT, refuse: str | None = None) -> str:
    argv = [*ping, "--socket", str(socket_path)]
    if refuse is not None:
        argv += ["--refuse-unanswered", refuse]
    argv += ["--wait-seconds", f"{wait_seconds:g}", event.event]
    if event.source:
        argv += ["--source", event.source]
    return shlex.join(argv)


def handler(cmd: str, timeout: int) -> dict:
    return {"type": "command", "command": cmd, "timeout": timeout, "async": False}


def hook_key(label: str, group: int = 0, index: int = 0) -> str:
    return f"{KEY_SOURCE}:{label}:{group}:{index}"


def trusted_hash(label: str, one: dict, matcher: str | None = None) -> str:
    """The hash Codex trusts one group's handler by: the group's `matcher` is hashed
    with it, and a group with none hashes `{event_name, hooks}` alone."""
    ident: dict = {"event_name": label, "hooks": [one]}
    if matcher is not None:
        ident["matcher"] = matcher
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def matcher(patterns) -> str | None:
    """The `PreToolUse` matcher selecting the tools `patterns` name, or None for none.

    Codex reads a matcher three ways: `*` or empty selects every tool; one of only
    letters, digits, `_` and `|` is an exact alternation of names; anything else is a
    regex found anywhere in the name. So: `*` when any pattern is `*`; `A|B` when every
    pattern is a plain name; else one anchored regex, each pattern's literal characters
    escaped and each `*` written `.*`. The matcher only narrows what Codex asks about;
    the host still matches each call itself."""
    names = list(dict.fromkeys(p for p in patterns if isinstance(p, str) and p))
    if not names:
        return None
    if EVERY_TOOL in names:
        return EVERY_TOOL
    if all(_PLAIN.fullmatch(p) for p in names):
        return "|".join(names)
    return "^(?:" + "|".join(_glob(p) for p in names) + ")$"


def refuse_table(entries) -> str | None:
    """The `--refuse-unanswered` value: the cover rule of each guard in `entries` (a
    host's table) that declared `on_error = "deny"`, as compact JSON, or None when none
    did."""
    rows = [{"calls": list(e.calls), "who": e.who,
             "match": dict(e.match) if e.match else None, "match_flags": e.match_flags}
            for e in entries if e.on_error == "deny" and e.calls]
    if not rows:
        return None
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def refuses(table: str, data: dict) -> bool:
    """Whether an unanswered `PreToolUse` ping refuses its call: when a row of `table`
    covers it, by the host's own rule (`guards.run.Entry.covers`). A table that doesn't
    read or compile, or a call with no tool name, refuses."""
    try:
        from .guards.run import Entry

        tool = data.get("tool_name")
        if not isinstance(tool, str) or not tool:
            return True
        agent_id = data.get("agent_id")
        agent_id = agent_id if isinstance(agent_id, str) and agent_id else None
        given = data.get("tool_input")
        given = given if isinstance(given, dict) else {}
        rows = json.loads(table)
        if not isinstance(rows, list):
            return True
        return any(Entry.of("refuse", row).covers(tool, agent_id, given) for row in rows)
    except Exception:  # noqa: BLE001 - a table it can't read fails closed
        return True


def _glob(pattern: str) -> str:
    return "".join(".*" if ch == "*" else "\\" + ch if ch in _REGEX_META else ch
                   for ch in pattern)


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(_toml(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{_toml_key(key)}={_toml(item)}" for key, item in value.items()
        ) + "}"
    raise TypeError(f"no TOML form for {value!r}")


def _toml_key(key: str) -> str:
    bare = key.replace("_", "").replace("-", "").isalnum()
    return key if bare else json.dumps(key)


def session_flags(ping: list[str], socket_path, *, hold_wait_s: float = PING_TIMEOUT - 1,
                  listen_wait_s: float = PING_TIMEOUT - 1,
                  pre_matcher: str | None = None, guard_wait_s: float = PING_TIMEOUT - 1,
                  refuse: str | None = None,
                  after_tool: bool = False, after_wait_s: float = PING_TIMEOUT - 1) \
        -> tuple[list[str], dict[str, str]]:
    """The launch's `-c` hook flags and the trust key and hash of each hook.

    `PreToolUse` is added only with a `pre_matcher` (`matcher`), its ping waiting
    `guard_wait_s` plus a margin, and carrying `refuse` (`refuse_table`) when given;
    `PostToolUse` only with `after_tool`, for every tool, its ping waiting `after_wait_s`
    plus the margin."""
    argv: list[str] = []
    keys: dict[str, str] = {}
    for event in EVENTS:
        if event.event == "Stop":
            wait_seconds = hold_wait_s + 1
        elif event.event == "Interrupt":
            wait_seconds = PING_TIMEOUT
        else:
            wait_seconds = listen_wait_s + 1
        one = handler(command(ping, socket_path, event, wait_seconds), event.timeout)
        argv += ["-c", f"hooks.{event.event}={_toml([{'hooks': [one]}])}"]
        keys[hook_key(event.label)] = trusted_hash(event.label, one)
    tools = []
    if pre_matcher is not None:
        tools.append((PRE_TOOL, pre_matcher, guard_wait_s, refuse))
    if after_tool:
        tools.append((POST_TOOL, EVERY_TOOL, after_wait_s, None))
    for event, match, wait_s, refusing in tools:
        one = handler(command(ping, socket_path, event, _ping_wait(wait_s), refusing),
                      event.timeout)
        argv += ["-c", f"hooks.{event.event}={_toml([{'matcher': match, 'hooks': [one]}])}"]
        keys[hook_key(event.label)] = trusted_hash(event.label, one, match)
    state = {key: {"trusted_hash": value} for key, value in keys.items()}
    argv += ["-c", f"hooks.state={_toml(state)}"]
    return argv, keys


def _ping_wait(wait_s: float) -> float:
    """A tool ping's wait: the host's bound plus the margin, in whole tenths so the
    command (and so its trust hash) is the same for the same bound."""
    return round(max(0.0, wait_s) + TOOL_MARGIN_S, 1)


def trust_problem(result, keys: dict[str, str]) -> str | None:
    listed: dict = {}
    for entry in (result or {}).get("data") or []:
        for hook in entry.get("hooks") or []:
            if hook.get("key") in keys:
                listed[hook["key"]] = hook
    for key in keys:
        hook = listed.get(key)
        if hook is None:
            return f"Codex does not list turns' hook {key}"
        if not hook.get("enabled"):
            return f"Codex lists turns' hook {key} as disabled"
        if hook.get("trustStatus") != TRUSTED:
            return (f"Codex lists turns' hook {key} as {hook.get('trustStatus')!r}, "
                    f"not trusted")
    return None


def hook_fields(data: dict, *, source: str | None = None, event: str | None = None) -> dict:
    """What the ping forwards of Codex's hook input. A subagent's hook carries its
    `agent_id`; every hook its `cwd` and `transcript_path`; a tool hook the tool's name
    and id, and `PreToolUse` its input; UserPromptSubmit the prompt's text."""
    fields: dict = {}
    for key in ("session_id", "turn_id", "agent_id", "cwd", "transcript_path",
                "tool_name", "tool_use_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            fields[key] = value
    if event == "PreToolUse" and isinstance(data.get("tool_input"), dict):
        fields["tool_input"] = data["tool_input"]
    if event == "UserPromptSubmit" and isinstance(data.get("prompt"), str):
        fields["prompt"] = data["prompt"]
    value = data.get("source")
    if isinstance(value, str) and value:
        fields["source"] = value
    elif source:
        fields["source"] = source
    active = data.get("stop_hook_active")
    if isinstance(active, bool):
        fields["stop_hook_active"] = active
    tasks = data.get("background_tasks")
    if isinstance(tasks, list):
        fields["task_types"] = tuple(
            item.get("type") if isinstance(item, dict)
            and isinstance(item.get("type"), str) else "unknown" for item in tasks
        )
    crons = data.get("session_crons")
    if isinstance(crons, list):
        fields["crons"] = sum(
            1 for cron in crons
            if not (isinstance(cron, dict) and cron.get("recurring") is True)
        )
    return fields


class HookServer:
    """One Unix socket receiving a Codex host's hook events: the five session hooks
    and, when the launch registered them, the two tool hooks."""

    def __init__(self, harness, socket_path, *, times: hooktimes.Recorder | None = None):
        self.harness = harness
        self.socket_path = Path(socket_path)
        #: The hook times the pings stamp, kept for the doctor.
        self.times = hooktimes.Recorder("codex") if times is None else times
        self.bound = False
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._event_lock = threading.RLock()

    def start(self) -> "HookServer":
        if self.socket_path.exists():
            verdict, error = protocol.probe(self.socket_path)
            if verdict == protocol.LIVE:
                raise RuntimeError(f"a hook server already listens on {self.socket_path}")
            if verdict != protocol.STALE:
                raise RuntimeError(f"could not prove {self.socket_path} stale: {error}")
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        self.bound = True
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(0.2)
        self._server = server
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="turns-codex-hooks",
        )
        self._thread.start()
        self.times.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        if self.bound:
            self.socket_path.unlink(missing_ok=True)
            self.bound = False
        self.times.close()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, _ = self._server.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket) -> None:
        conn = protocol.LineConn(sock)
        try:
            frame = conn.recv(PING_TIMEOUT)
            if not isinstance(frame, dict):
                return
            if frame.get("event") in TOOL_EVENT_NAMES:
                # Tool hooks share no lock: parallel calls are judged together.
                reply = self._tool(frame)
            else:
                with self._event_lock:
                    reply = self._dispatch(frame, conn)
            if reply is not None:
                conn.send(reply)
                self._timed(frame)
        except (OSError, TimeoutError, protocol.ProtocolError):
            pass
        finally:
            conn.close()

    def _dispatch(self, frame: dict, conn: protocol.LineConn) -> dict | None:
        event = frame.get("event")
        if event not in EVENT_NAMES:
            return {"ok": False, "error": f"unknown Codex hook event {event!r}"}
        session_id = frame.get("session_id")
        if session_id != self.harness.session_id:
            self.harness.foreign_hook(event, session_id)
            return {"ok": True}
        endpoint = self.harness.endpoint
        if event in ("SessionStart", "PostCompact"):
            source = frame.get("source")
            source = source if isinstance(source, str) else None
            start_source = getattr(self.harness, "start_source", None)
            if event == "SessionStart" and start_source is not None:
                # Codex says `resume` at the first turn of a thread reloaded before it
                # had any; while the thread has had no turn, that is its start.
                source = start_source(source)
            # The accepted load raised this start's or resume's Ready.
            loaded = event == "SessionStart" and source in ("startup", "resume")
            # The guards are read again at each ready; their notes join its context.
            guards_ready = getattr(self.harness, "guards_ready", None)
            notes = guards_ready() if guards_ready is not None else []
            context = endpoint.ready(source, self.harness.session_id, announce=not loaded)
            if not loaded:
                # The load settled the MCP servers at its own ready; this one settles them
                # again (a compaction's two readys each do, or join a settle running).
                mcp_ready = getattr(self.harness, "mcp_ready", None)
                if mcp_ready is not None:
                    mcp_ready()
            if notes:
                from .listeners import JOIN

                context = JOIN.join([*([context] if context else []), "\n".join(notes)])
            if source == "compact":
                # A compaction the host sent took effect: its shell stop may begin.
                compacted = getattr(self.harness, "compacted", None)
                if compacted is not None:
                    compacted()
            reply = {"ok": True, "context": context}
            start_answered = getattr(self.harness, "start_answered", None)
            if event == "SessionStart" and start_answered is not None:
                # The mark clears only once the answer is out: a send that fails leaves
                # it, as if this start had not been answered.
                conn.send(reply)
                self._timed(frame)
                start_answered()
                return None
            return reply
        if event == "UserPromptSubmit":
            if frame.get("agent_id"):
                # A subagent's prompt is its task, not the main loop's prompt.
                return {"ok": True}
            text = frame.get("prompt")
            text = text if isinstance(text, str) else None
            seen = getattr(self.harness, "prompt_seen", None)
            if seen is not None:
                seen(frame.get("turn_id"), text)
            return {"ok": True, "context": endpoint.prompt(text)}
        # A hook answers what it is asked and closes no turn. Stop is Codex
        # asking whether the turn may end: the turn runs on until Codex's own
        # turn/completed, which carries the outcome (`interrupted` for Esc).
        if event == "Interrupt":
            return {"ok": True}
        facts = self.harness.stop_facts(frame)
        reason = endpoint.on_stop(facts)
        if reason:
            conn.send({"ok": True, "block": reason})
            self._timed(frame)
            # The ping still says when it has printed the block. Whether it did or not,
            # the turn's end is Codex's to report.
            try:
                conn.recv(PING_TIMEOUT)
            except (TimeoutError, protocol.ProtocolError):
                pass
            return None
        # Unblocked: once Codex reads this answer it may complete the turn at once, and
        # a steer it takes then can be written into the turn and never answered. The
        # turn is marked ending before the answer goes out.
        self.harness.stop_answered(facts.turn_id)
        return {"ok": True}


    def _tool(self, frame: dict) -> dict:
        """A tool hook: the host's `pre_tool` or `post_tool` answers it. One from another
        thread, or a host without the hook, passes the call with nothing to add. A host
        fault answers not-ok, which the ping takes as no answer: it refuses a call a deny
        guard covers and passes any other."""
        session_id = frame.get("session_id")
        if session_id != self.harness.session_id:
            self.harness.foreign_hook(frame.get("event"), session_id)
            return {"ok": True}
        name = "pre_tool" if frame.get("event") == "PreToolUse" else "post_tool"
        answer = getattr(self.harness, name, None)
        if answer is None:
            return {"ok": True}
        try:
            return {"ok": True, **answer(frame)}
        except Exception as exc:  # noqa: BLE001 - answered as no answer, like a dead host
            log.exception("the Codex %s hook failed", frame.get("event"))
            return {"ok": False, "error": f"the host's {name} failed: {type(exc).__name__}"}

    def _timed(self, frame: dict) -> None:
        """Record the hook's wait: from the ping's stamp to this answer. A frame with no
        stamp (an older ping) and one from another session record nothing. A tool hook's
        wait is recorded as `guard` or `after-tool`."""
        stamp = frame.get("stamp")
        if frame.get("session_id") != self.harness.session_id:
            return
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            return
        event = frame.get("event")
        self.times.add(TIMED_AS.get(event, event), (time.time() - stamp) * 1000)


class _Ping:
    def __init__(self, path: Path, event: str, source: str | None,
                 wait_seconds: float, stamp: float | None = None,
                 refuse: str | None = None):
        self.path = path
        #: The deny guards' cover rule (`refuse_table`), on a `PreToolUse` ping only.
        self.refuse = refuse
        #: The hook's input, kept for the refusal of an unanswered call.
        self.data: dict = {}
        #: Whether the hook ran to its end: something printed, or silence chosen.
        self.answered = False
        self.stamp = stamp
        self.event = event
        self.source = source
        self.wait_seconds = wait_seconds
        self.conn: protocol.LineConn | None = None
        self.block = False
        self.deny = False

    def body(self, data: dict) -> str | None:
        self.data = data
        self.conn = protocol.connect(self.path, PING_TIMEOUT)
        ask = {"event": self.event, **hook_fields(data, source=self.source,
                                                  event=self.event)}
        if self.stamp is not None:
            ask["stamp"] = self.stamp
        self.conn.send(ask)
        reply = self.conn.recv(self.wait_seconds)
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise RuntimeError((reply or {}).get("error", "the hook host did not answer"))
        reason = reply.get("block")
        if isinstance(reason, str) and reason:
            self.block = True
            return reason
        reason = reply.get("deny")
        if self.event == "PreToolUse" and isinstance(reason, str) and reason:
            self.deny = True
            return reason
        context = reply.get("context")
        return context if isinstance(context, str) and context else None

    def render(self, event: str, text: str) -> str:
        """`PreToolUse`'s output: a refusal, or text for the model."""
        if self.deny:
            return _deny(event, text)
        from .hooks.wire import envelope

        return envelope(event, text)

    def emitted(self, text) -> None:
        self.answered = True
        try:
            if self.block and self.conn is not None:
                self.conn.send({"op": PRINTED})
        finally:
            self.close()

    def error(self, exc, *, where) -> None:
        log.warning("Codex hook %s failed at %s: %s", self.event, where, exc)
        self.close()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def unanswered(self) -> str | None:
        """The refusal to print once the hook has ended with no answer, or None: only a
        `PreToolUse` ping carrying a table, for a call that table covers."""
        if self.answered or self.event != "PreToolUse" or self.refuse is None:
            return None
        return _deny(self.event, CANNOT_CHECK) if refuses(self.refuse, self.data) else None


def _block(event: str, reason: str) -> str:
    return json.dumps({"decision": "block", "reason": reason}, separators=(",", ":"))


def _deny(event: str, reason: str) -> str:
    """A `PreToolUse` refusal in Codex's hook output form; the model reads `reason`."""
    if not isinstance(reason, str):
        raise TypeError("a refusal's reason must be a string")
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "permissionDecision": "deny",
        "permissionDecisionReason": reason}}, separators=(",", ":"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moxi-turns-ping")
    parser.add_argument("event", choices=PING_EVENTS)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--source")
    parser.add_argument("--wait-seconds", type=float, default=PING_TIMEOUT,
                        help=argparse.SUPPRESS)
    parser.add_argument("--refuse-unanswered", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.wait_seconds <= 0:
        parser.error("--wait-seconds must be greater than zero")

    from .hooks.run import run

    from .hooks.run import emit

    ping = _Ping(Path(args.socket), args.event, args.source, args.wait_seconds, _STARTED,
                 args.refuse_unanswered)
    render = _block if args.event == "Stop" else \
        ping.render if args.event == "PreToolUse" else None
    kwargs = {"render": render} if render is not None else {}
    run(args.event, ping.body, seconds=args.wait_seconds, on_error=ping.error,
        on_emitted=ping.emitted, **kwargs)
    # The wall is down and nothing was printed: a covered call is refused here.
    refusal = ping.unanswered()
    if refusal is not None:
        try:
            emit(refusal)
        except OSError as exc:
            log.warning("Codex hook %s could not print its refusal: %s", args.event, exc)
    return 0


__all__ = [
    "EVENTS", "EVENT_NAMES", "EVERY_TOOL", "HookEvent", "HookServer", "HooksUntrusted",
    "KEY_SOURCE", "PING_EVENTS", "POST_TOOL", "PRE_TOOL", "TOOL_EVENTS", "TOOL_EVENT_NAMES",
    "CANNOT_CHECK", "TRUSTED", "command", "handler", "hook_fields", "hook_key",
    "hooks_socket", "main", "matcher", "refuse_table", "refuses", "session_flags",
    "trust_problem", "trusted_hash",
]

"""The Claude endpoint's leg to its own runner: turns' internal protocol, not the contract.

The runner (the `moxi-turns` plugin) spawns the endpoint at `session.start` with
`$.process.spawn`. The two talk over two channels:

- **Endpoint to runner: JSON lines on the endpoint's stdout**, one object per line, each
  with an `"op"`. The child's stdin is a fixed string, so nothing comes back that way.
- **Runner (and gate) to endpoint: HTTP POSTs with JSON bodies to `mod.sock`**, the
  second socket in the endpoint's directory, because `$.http.fetch` speaks HTTP. Every
  answer is `200` with a JSON object, or `4xx` with `{"ok": false, "error": …}`.

The runner and the endpoint ship in one package and `hello.json` names both versions, so
this leg changes only with the package. `LEG` is its version; the endpoint's first line
names it and the runner sends nothing to an endpoint whose leg it does not speak.

Lines (endpoint to runner)
--------------------------

The first line, always first, before the runner sends anything:

    {"op": "hello", "leg": 3, "mod_sock": "<absolute path>", "stop_wait_ms": n,
     "listen_wait_ms": n, "endpoint_version": "…", "pid": <endpoint pid>,
     "stop_after_clear": bool, "guards": [<entry>, ...], "after_tool_wait_ms": n,
     "floors": {"<row id>": n, ...}}

`stop_after_clear` says whether the runner stops a `clear`'s shells through `TaskStop`
after the clear (`STOP_AFTER_CLEAR`); false, it stops none after a clear and reports
every listed one left, why `not-stopped-after-clear`.

`guards` is the guard table for Claude, which the runner matches every tool call against
in its own process (`moxi_turns.guards.run` states the rule). Each entry:

    {"id": "<module>.<name>", "calls": ["<pattern>", ...], "who": "any" | "main" | "agents",
     "match": {"<input field>": "<regex>"} | null, "match_flags": "" | "i",
     "judge": bool, "deny": "<text>", "wants": ["prompts"]?, "on_error": "allow" | "deny",
     "deadline_ms": n}

`deadline_ms` is already clamped to `guards.max_wait_ms`. A covering entry with no judge
refuses with its `deny` and no trip; covering judge entries are asked on `/ask/guard`.

`floors` names every after-tool row (a listener row naming `after-tool`) by its id, with
its floor in tokens (0 for a row with no floor, or before its first read). An empty map
means no row: the runner never asks `/ask/after-tool`. After a tool call the runner asks
the rows the floors let through: on the main loop (no `agentId`), a row whose floor is
above the context's tokens from the last `session.measure` is skipped; an agent's call,
or a context not known yet (none since the last SessionStart), skips none.
`after_tool_wait_ms` is `after_tool.wait_ms`, the bound the endpoint keeps on the rows.

or, when the endpoint cannot start (its config is broken), its only line:

    {"op": "failed", "leg": 3, "error": "<words>"}

Then, in any order:

    {"op": "submit", "delivery": id, "kind": "start" | "deliver", "text": "…",
     "nonce": "<the window nonce>"}
        `$.prompt.submit` the text, framed under the plugin's name, with the line
        `NONCE_LINE + nonce` first (entry, below). The window nonce is random, 128 bits,
        minted per window, so no other window of this endpoint or any earlier one names
        it. The endpoint sends the next `submit` only once this one's window has closed.
    {"op": "append", "delivery": id, "text": "…"}
        `$.session.append` a user-role row into the running turn (a steer). The runner
        reports `append.done` when the call resolves without a deny, else
        `append.failed`.
    {"op": "command", "request": id, "name": "clear" | "exit" | "compact",
     "stop_shells": bool}
        `$.command.run` the command; the gate re-checks it (`/gate/command`). With
        `stop_shells` (never with `exit`), before the run the runner asks
        `$.tool.check` for a `TaskStop` of each background shell and Monitor it lists,
        and any answer but `allow` reports `refused` `unsupported` with nothing run.
        Once the run returns without the gate's withdrawal, it stops each listed one
        with `$.tool.call` `TaskStop` within `SHELL_STOP_S` and reports `shells`.
    {"op": "live", "ask": n}
        Read `$.agent.list()` and `$.prompt.read()` and answer on `/live`.
    {"op": "agents", "ask": n}
        Read `$.agent.list()` and answer on `/agents`, each entry as the engine gives it.
    {"op": "guards", "table": [<entry>, ...]}
        The guard table again, read at a `ready` (start, resume, clear, compaction); it
        replaces the one the runner holds. Sent before the `ready`'s `/ask/context` answer.
    {"op": "floors", "rows": {"<row id>": n, ...}}
        Every after-tool row's floor, sent each time one changes: at a `ready` (before
        its `/ask/context` answer) or after a call of the row's listener. It replaces the
        map the runner holds.

Requests (runner and gate to endpoint, `POST` on `mod.sock`)
------------------------------------------------------------

`/report` — one report from the runner's ordered queue. Each carries `seq`, the queue's
own count from 1 for this load, and `kind`:

    session        {session_id, mod_version}       the runner's `$.session.id()`
    turn.start     {turn_id}                        a main-loop `turn.start`
    turn.complete  {turn_id, reason, is_aborted}    a main-loop `turn.complete`
    submit.close   {delivery, end, reason}          a submit settled: `end` is `resolve`,
                                                    `drop` or `fail` (it rejected); a
                                                    drop's `reason` is the gate's code read
                                                    from its line, else `dropped`. Sent
                                                    whenever it settles, after a bound's
                                                    report too
    submit.bound   {delivery, reason}               the runner's bound: still unsettled
                                                    after 5 s with no main-loop turn open
    append.done    {delivery}                       a steer appended: `$.session.append`
                                                    resolved without a deny
    append.failed  {delivery, reason}               a steer the runner could not append
    command.done   {request, state, reason, new_session_id, shells?}
                                                    `$.command.run` settled: `state` is
                                                    `ran`, `refused` or `failed`. With
                                                    `stop_shells` a `ran` carries
                                                    `shells: {stopped: [id], left: [{id,
                                                    why}], complete}`, sent once the
                                                    stop is done

Reports are keyed by their window's delivery: an end for a window that is not the open
one is logged and changes nothing. The bound is a report, not a close: the endpoint fails
the open window by it only while its prompt is not admitted (entry, below).

A report is applied once, in `seq` order. One whose `seq` the endpoint has already
applied is answered `ok` and changes nothing, so the runner may send a report again when
its answer missed the runner's bound. A `seq` past the next one expected means a report
was lost: the endpoint fails the open submit window (its delivery reads `Lost`, reason
`untied: …`) rather than tie a delivery from a partial record, then applies it. Answer:
`{"ok": true}`.

`/live` — `{ask, agents, draft}` (or `{ask, error}`), the answer to a `live` line.
`agents` is null when `$.agent.list()` could not be read: unknown, which counts as busy
and never as 0. An unreadable box is sent as `draft: true`. Answer `{"ok": true}`.

`/agents` — `{ask, entries}` (or `{ask, error}`), the answer to an `agents` line. `entries`
is `$.agent.list()` unmapped, each `{id, type, description, status, parentId?,
teammateId?}`; the endpoint maps the states. `entries` is null when the list could not be
read: the list is then not complete, which counts as busy. Answer `{"ok": true}`.

`/ask/stop` — `{turn_id, session_id, stop_hook_active, task_types, crons}` at a main-loop
`classic.Stop`. Answer `{"ok": true, "reason": "<text>" | null}`: a reason holds the turn
open (`block`), null lets it end. The endpoint answers within `stop_handler.wait_ms`.

`/ask/context` — `{event: "ready" | "prompt", source, session_id}` at
`classic.SessionStart` (`ready`) and `classic.UserPromptSubmit` (`prompt`). A `ready` also
carries `transcript_path`, the file Claude writes the session's records to (the hook
input's); a `prompt` carries `text`, the prompt as the hook got it, and `origin`, its
origin's kind as the runner's `prompt.submit` hook recorded it, or null when the runner
can't tie the two. Answer `{"ok": true, "context": "<text>" | null}`, the event's
`additionalContext`. The endpoint answers within `listen.wait_ms`.

`/ask/after-tool` — `{call: {tool, tool_use_id, agentId, is_error}, rows: [id, ...]}` after
a tool call ran (`next(e)` resolved with a result or an error, not a deny), naming the
rows the floors let through. The endpoint asks those rows, bounded together by
`after_tool.wait_ms` from the ask's arrival; an id it doesn't know is skipped. Answer
`{"ok": true, "context": "<text>" | null}`: the rows' texts joined, which go into the
result's `context` after any guard lines. The runner bounds the trip by
`after_tool_wait_ms` plus its margin.

`/report/usage` — `{usage}`, a usage reading as Claude gave it, unparsed: the figures of a
`session.measure` whose `changed` names `context` or `rateLimits`, and at each main-loop
`turn.complete` what `$.session.usage()` answers, never a `breakdown`. Outside the report
queue; the endpoint tells the `usage` rows with the time it arrived. Answer
`{"ok": true}`.

`/gate/entry` — `{nonce, draft, agents}` from the gate, for every runner prompt as it
enters. `nonce` is the window nonce the gate read off the prompt's first
line; `draft` (bool) and `agents` (int, or true when the list could not be read) are the
gate's readings. The endpoint admits the prompt only if the nonce names the window now
open and that window has admitted nothing yet; for a start it then re-checks the ledger
and the refusals the start asked for, with the readings. It records its answer before it
sends it. Answer `{"ok": true, "verdict": "pass"}` (the gate takes the nonce line off and
calls `next`) or `{"ok": true, "verdict": "drop", "reason": r, "text": "<one line for the
person>"}`; the gate drops a `drop` with `text` and never calls `next`. A nonce that names
no open window reads `late`, and the endpoint logs the late entry.

`/ask/guard` — `{call: {tool, tool_use_id, input, agentId, cwd?, prompts?}, guards: [id,
...]}` for a tool call that judge entries of the table cover: `input` is the call's envelope
less `tool`, `tool_use_id` and `agentId` (absent or null on the main loop); `cwd`, the
directory the call runs in, `$.session.cwd()` read in the call's hook (a subagent's own in
a subagent, and meant to follow the Bash tool's `cd`s), absent when that read failed, and
the endpoint judges against the launch directory when `cwd` is absent or not absolute;
`prompts`, the current main turn's prompts `[{origin, text}]`, when an asked entry `wants`
them. The endpoint judges each named entry within its own `deadline_ms` from the ask's arrival, so
the answer comes within the largest of them; the runner bounds the trip by that plus its
margin. An id not on the endpoint's table (it changed at a `ready`) is skipped. Answer
`{"ok": true, "deny"?: "<reason>", "lines": ["<text>", ...], "broken": ["<text>", ...]}`:
`deny` refuses the call; else `lines` (the judges' allow lines) and `broken` (one line
per guard that broke and let the call through) go after the tool's result, in that order.
A trip that fails or overruns breaks every entry it asked: the runner refuses with
"turns could not check this call with guard <id> (<why>)" when one declared `on_error`
`deny`, else adds the line "[turns] guard <id> could not check this call (<why>); it ran
unchecked." per entry.

`/hook-times` — `{samples: [{event, ms}]}` from the runner or the gate, each at most
once a minute: how long each of its hook handlers took, entry to return less the time in
`next`. The gate's events carry the prefix `gate `. The leg keeps them for the doctor
(`hooktimes`), apart from the report queue, so a lost batch costs only its samples.
Answer `{"ok": true}`.

`/gate/command` — `{request, agents, draft}` from the gate, for a command its runner
queued, as it comes off the queue. The endpoint re-checks it and records its decision
before it answers, as `/gate/entry` does.
"""

from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading
from collections.abc import Callable
from typing import Any, BinaryIO

from . import hooktimes

log = logging.getLogger("moxi.turns.claude")

#: This leg's version.
LEG = 3

#: Whether the runner stops a `clear`'s shells after the clear. Claude Code may no longer
#: find a task through `TaskStop` once `/clear` has run; until a live session shows it
#: does, this stays as the design has it, and turning it off keeps the order (the stop
#: after the command) and stops nothing after a clear.
STOP_AFTER_CLEAR = True
#: The `why` of a listed shell left running because the runner stops none after a clear.
NOT_STOPPED_AFTER_CLEAR = "not-stopped-after-clear"

#: The endpoint's line ops.
LINE_HELLO, LINE_FAILED = "hello", "failed"
LINE_SUBMIT, LINE_APPEND, LINE_COMMAND, LINE_LIVE = "submit", "append", "command", "live"
LINE_AGENTS, LINE_GUARDS, LINE_FLOORS = "agents", "guards", "floors"

#: The request paths on `mod.sock`.
PATH_REPORT = "/report"
PATH_LIVE = "/live"
PATH_AGENTS = "/agents"
PATH_STOP = "/ask/stop"
PATH_CONTEXT = "/ask/context"
PATH_GUARD = "/ask/guard"
PATH_AFTER_TOOL = "/ask/after-tool"
PATH_USAGE = "/report/usage"
PATH_GATE_ENTRY = "/gate/entry"
PATH_GATE_COMMAND = "/gate/command"
PATH_HOOK_TIMES = "/hook-times"
PATHS = (PATH_REPORT, PATH_LIVE, PATH_AGENTS, PATH_STOP, PATH_CONTEXT, PATH_GUARD,
         PATH_AFTER_TOOL, PATH_USAGE, PATH_GATE_ENTRY, PATH_GATE_COMMAND, PATH_HOOK_TIMES)

#: The report kinds.
R_SESSION, R_TURN_START, R_TURN_COMPLETE = "session", "turn.start", "turn.complete"
R_SUBMIT_CLOSE, R_SUBMIT_BOUND = "submit.close", "submit.bound"
R_APPEND_DONE, R_APPEND_FAILED = "append.done", "append.failed"
R_COMMAND_DONE = "command.done"
REPORTS = (R_SESSION, R_TURN_START, R_TURN_COMPLETE, R_SUBMIT_CLOSE, R_SUBMIT_BOUND,
           R_APPEND_DONE, R_APPEND_FAILED, R_COMMAND_DONE)

#: A submit window's three ends.
END_RESOLVE, END_DROP, END_FAIL = "resolve", "drop", "fail"
ENDS = (END_RESOLVE, END_DROP, END_FAIL)

#: The refusal codes the plugins decide on their own, with no word from the endpoint: the
#: gate's `gone` (no answer within its 1 s, no `mod.sock` path, a fault in its hook) and
#: the runner's `unsupported` (the gate is not loaded, so nothing was submitted). A drop
#: whose reason is one of these reads `Refused` with it; any other drop the endpoint did
#: not record reads `dropped`, another plugin's.
GATE_CODES = ("gone", "unsupported")

#: The first line of every runner prompt is this prefix and the window nonce; the gate
#: takes the line off as it admits the prompt, so the model never reads it.
NONCE_LINE = "moxi-turns-window: "
#: What the endpoint answers for a runner prompt whose nonce names no open window: a
#: closed window's, an earlier endpoint's, or one already admitted.
LATE = "late"

#: The longest request body the endpoint reads.
MAX_BODY = 4 * 1024 * 1024


class BadRequest(Exception):
    """A request the endpoint cannot read; answered 400."""


class LineWriter:
    """The endpoint's half of the leg: one JSON object per line on its private stdout.

    A write that fails (EPIPE: the runner is gone) calls `on_broken` once and every later
    write is dropped, since nobody reads them.
    """

    def __init__(self, out: BinaryIO, on_broken: Callable[[str], None] | None = None):
        self._out = out
        self._lock = threading.Lock()
        self._broken = False
        self._on_broken = on_broken

    @property
    def broken(self) -> bool:
        return self._broken

    def write(self, obj: dict[str, Any]) -> bool:
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self._lock:
            if self._broken:
                return False
            try:
                self._out.write(data)
                self._out.flush()
                return True
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._broken = True
                reason = f"EPIPE on the runner's line: {exc}"
        log.info("%s", reason)
        if self._on_broken is not None:
            self._on_broken(reason)
        return False


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class LegServer:
    """The HTTP server on `mod.sock`. `handle(path, body)` answers each request with a
    dict (sent as `200`), or raises `BadRequest` (`400`). The server answers
    `/hook-times` itself, into `times`."""

    def __init__(self, path: str, handle: Callable[[str, dict[str, Any]], dict[str, Any]],
                 *, times: hooktimes.Recorder | None = None):
        self.path = path
        self.times = hooktimes.Recorder("claude") if times is None else times
        handler = _handler_for(_with_hook_times(handle, self.times))
        self._server = _Server(path, handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.2}, daemon=True,
                                        name="turns-claude-leg")

    def start(self) -> "LegServer":
        self._thread.start()
        self.times.start()
        return self

    def close(self) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()
            self.times.close()


def _with_hook_times(handle, times: hooktimes.Recorder):
    def answer(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path != PATH_HOOK_TIMES:
            return handle(path, body)
        samples = body.get("samples")
        if not isinstance(samples, list):
            raise BadRequest("hook times without a samples list")
        for sample in samples:
            if isinstance(sample, dict):
                times.add(sample.get("event"), sample.get("ms"))
        return {}

    return answer


def _handler_for(handle):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "moxi-turns-endpoint"

        def address_string(self) -> str:
            return "runner"

        def log_message(self, fmt, *args) -> None:  # noqa: D401 - quiet by design
            log.debug("leg: " + fmt, *args)

        def do_POST(self) -> None:  # noqa: N802 - http.server's name
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_BODY:
                    raise BadRequest(f"a body of {length} bytes")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise BadRequest(f"a body that is not JSON: {exc}") from None
                if not isinstance(body, dict):
                    raise BadRequest("a body that is not a JSON object")
                if self.path not in PATHS:
                    self._send(404, {"ok": False, "error": f"no such path {self.path!r}"})
                    return
                answer = handle(self.path, body)
                self._send(200, {"ok": True, **answer})
            except BadRequest as exc:
                self._send(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - one bad request never ends the leg
                log.exception("leg request %s failed", self.path)
                self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, {"ok": False, "error": "POST only"})

        def _send(self, status: int, obj: dict[str, Any]) -> None:
            data = json.dumps(obj, separators=(",", ":")).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass              # the runner gave up on this request (its own bound)

    return Handler


__all__ = ["LEG", "STOP_AFTER_CLEAR", "NOT_STOPPED_AFTER_CLEAR", "LINE_HELLO", "LINE_FAILED", "LINE_SUBMIT", "LINE_APPEND", "LINE_COMMAND",
           "LINE_LIVE", "LINE_AGENTS", "LINE_GUARDS", "LINE_FLOORS", "PATH_REPORT",
           "PATH_LIVE", "PATH_AGENTS", "PATH_STOP", "PATH_CONTEXT", "PATH_GUARD",
           "PATH_AFTER_TOOL", "PATH_USAGE",
           "PATH_GATE_ENTRY", "PATH_GATE_COMMAND", "PATH_HOOK_TIMES", "PATHS", "R_SESSION",
           "R_TURN_START", "R_TURN_COMPLETE", "R_SUBMIT_CLOSE", "R_SUBMIT_BOUND", "R_APPEND_DONE",
           "R_APPEND_FAILED",
           "R_COMMAND_DONE", "REPORTS", "END_RESOLVE", "END_DROP", "END_FAIL", "ENDS",
           "GATE_CODES", "NONCE_LINE", "LATE", "MAX_BODY",
           "BadRequest", "LineWriter", "LegServer"]

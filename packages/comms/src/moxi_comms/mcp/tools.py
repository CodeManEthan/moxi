"""comms' six tools, as a session's model calls them over MCP: `send`, `reply`, `agents`,
`whoami`, `history` and `remove`.

Each tool is the `moxi comms` verb of the same name, run inside the daemon:

  * **who calls.** The identity is the one the bearer check placed the call as (the
    token's row), read from the SDK's request context on the loop. No tool has a
    parameter naming the sender, so a session sends only as itself.
  * **where it runs.** The async part, on the loop, reads the identity and hands a
    synchronous body to the daemon's tool pool (`inprocess.Calls`); the body runs the
    verb's own code (`sending.run`, `sending.delete`, the daemon's reads) through the
    in-process client, on that one pool thread. Every check the verb makes runs.
  * **what comes back.** A `CallToolResult` with one text part, the lines the verb
    prints for the same outcome, and `structuredContent` with typed fields under each
    tool's declared `outputSchema`. A refusal is `isError` with the refusal's text and
    a `refusal` field naming its code; a tool never raises for one. A send whose answer
    never came is outcome `unknown`, and says to check history before sending again.
  * **cures.** A refusal or note names its cure in the tools' words (`sending.Context`
    with `cures="tool"`): `new_name: true`, `new: true`, the reply tool with a
    `message_id`, never a command-line flag the tools don't have.

The schemas are written out here, not derived from the functions' signatures: the
derived ones carry a title on every field, and the whole list is kept small, since
Codex loads it into every thread up front. The signatures still validate the arguments.
"""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import CallToolResult, TextContent

from .. import attribution, config, directory, machine_line, sending
from ..client import DaemonError, DaemonOffline
from ..provision import NAMES
from ..roster_view import SEND_ONLY, render_agents
from ..scoping import identity_of, is_auto_name, split_tag, tag_matches
from ..store import books
from . import inprocess

log = logging.getLogger("comms.mcp.tools")

#: The six tools, in the order `tools/list` gives them.
NAMES_OF_TOOLS = ("send", "reply", "agents", "whoami", "history", "remove")

#: Seconds every daemon call and record read of one tool call may take.
DEADLINE = 30.0

#: `history`'s bounds on `limit`, and its default.
HISTORY_LIMIT = (1, 200)
HISTORY_DEFAULT = 20

#: What a tool says when its session's binding isn't live: the daemon restarted and the
#: adapter hasn't bound again, or its connection dropped.
RECONNECTING = ("comms is reconnecting this session; try again in a few seconds. "
                "Nothing was sent.")
RECONNECTING_DELETE = ("comms is reconnecting this session; try again in a few seconds. "
                       "Nothing was withdrawn.")
#: A tool body that failed in a way comms didn't foresee. The exception's own text stays
#: in the daemon's log.
FAILED = "comms couldn't finish this call; it is in comms' log. Try again in a moment."

POSTURES = ("none", "optional", "once", "open")
Posture = Literal["none", "optional", "once", "open"]


# --------------------------------------------------------------------------- the texts

def descriptions(expiry: str = "24h") -> dict[str, str]:
    """Each tool's description, as the agent reads it. `expiry` is the config's
    `[delivery] expiry`, the expiry of a send that sets none."""
    spoken = _spoken(expiry)
    expires = (f"An undelivered message expires after {spoken} unless you set expires; "
               "nobody is told when one expires." if spoken else
               "An undelivered message waits until it is delivered unless you set "
               "expires; nobody is told when one expires.")
    return {
        "send": (
            "Send a message to agents or the operator by name. posture says what you "
            "want back: none (no reply), optional, once (one answer), or open (a "
            "back-and-forth). It waits in each recipient's inbox until they are idle. "
            "Set interrupt to reach a busy recipient between its tool calls (on OpenCode "
            f"it still waits for idle). {expires} Set new when a recipient owes you a "
            "reply and this is something else. Set new_name to message a name that "
            "hasn't started yet."),
        "reply": (
            "Reply to a message by its id. The reply goes to whoever sent it, or for "
            "your own message to its recipients. A reply to a once message asks nothing "
            "back unless you set a posture; in an open exchange it stays open. expires "
            "and interrupt work as in send."),
        "agents": (
            "List the agents you can message: each name, whether its session is running "
            "now, and its harness."),
        "whoami": (
            "Show the identity this session holds: the name others use to message you. "
            "An identity is a name only one session can hold at a time."),
        "history": (
            "Read recent messages to and from an identity, newest last, with who really "
            "sent each. A message that expired or was withdrawn is shown as such."),
        "remove": (
            "Withdraw a message you sent from every recipient who hasn't had it yet. One "
            "already delivered can't be withdrawn; the result says who had it."),
    }


def _spoken(expiry: str) -> str | None:
    """`24h` as `24 hours`, in the unit it is written in; None for `never`."""
    try:
        seconds = config.parse_duration(expiry)
    except ValueError:
        return "24 hours"
    if seconds is None:
        return None
    raw = str(expiry).strip().lower()
    n = int(raw[:-1])
    unit = {"s": "second", "m": "minute", "h": "hour", "d": "day"}[raw[-1]]
    return f"{n} {unit}" + ("" if n == 1 else "s")


# --------------------------------------------------------------------------- the schemas

_STR = {"type": "string"}
_BOOL = {"type": "boolean", "default": False}
_ID = {"type": "string", "description": "a message id"}
_EXPIRES = {"type": "string", "description": "30m, 6h, 3d or never"}
_STRS = {"type": "array", "items": _STR}
_NULL_STR = {"type": ["string", "null"]}

#: Each tool's `inputSchema`.
INPUTS: dict[str, dict] = {
    "send": {"type": "object", "properties": {
        "to": {"type": "array", "items": _STR, "minItems": 1,
               "description": "names to send to"},
        "text": _STR,
        "posture": {"type": "string", "enum": list(POSTURES)},
        "expires": _EXPIRES,
        "interrupt": _BOOL,
        "reply_to": _ID,
        "new": _BOOL,
        "new_name": _BOOL,
    }, "required": ["to", "text", "posture"]},
    "reply": {"type": "object", "properties": {
        "message_id": _ID,
        "text": _STR,
        "posture": {"type": "string", "enum": list(POSTURES),
                    "description": "unset: a reply to a once message asks nothing back; "
                                   "in an open exchange it stays open"},
        "expires": _EXPIRES,
        "interrupt": _BOOL,
    }, "required": ["message_id", "text"]},
    "agents": {"type": "object", "properties": {}},
    "whoami": {"type": "object", "properties": {}},
    "history": {"type": "object", "properties": {
        "identity": {"type": "string", "description": "default: yours"},
        "limit": {"type": "integer", "minimum": HISTORY_LIMIT[0],
                  "maximum": HISTORY_LIMIT[1], "default": HISTORY_DEFAULT},
    }},
    "remove": {"type": "object", "properties": {"message_id": _ID},
               "required": ["message_id"]},
}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties)}


_SENT = _object({
    "outcome": {"type": "string", "enum": [sending.OUTCOME_SENT, sending.OUTCOME_UNKNOWN]},
    "message_id": _NULL_STR,
    "recipients": {"type": "array", "items": _object({
        "name": _STR, "delivery": {"type": "string", "enum": list(sending.DELIVERIES)}})},
    "expires_at": {"type": ["integer", "null"]},
    "interrupt": {"type": "boolean"},
    "notes": _STRS,
})

#: Each tool's `outputSchema`: the `structuredContent` of a result that isn't an error.
OUTPUTS: dict[str, dict] = {
    "send": _SENT,
    "reply": _SENT,
    "agents": _object({"agents": {"type": "array", "items": _object({
        "name": _STR, "running": {"type": "boolean"}, "harness": _NULL_STR,
        "send_only": {"type": "boolean"}})}}),
    "whoami": _object({"identity": _STR, "name": _STR, "scope": _STR,
                       "harness": _NULL_STR, "session_id": _NULL_STR}),
    "history": _object({"messages": {"type": "array", "items": _object({
        "id": _STR, "from": _STR, "to": _STRS, "text": _STR, "at": _STR,
        "attribution": _STR,
        "state": {"type": "string",
                  "enum": ["delivered", "waiting", "expired", "withdrawn"]}})}}),
    "remove": _object({"message_id": _STR, "withdrawn": _STRS, "delivered": _STRS,
                       "expired": _STRS}),
}


# --------------------------------------------------------------------------- results

def _result(text: str, structured: dict, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structured_content=structured, is_error=error)


def refused(code: str, text: str, **fields) -> CallToolResult:
    """An error result: the refusal's text, and its code word as `refusal`."""
    return _result(text, {"refusal": code, **fields}, error=True)


def _send_result(daemon, result: sending.SendResult, interrupt: bool) -> CallToolResult:
    """A send's or a reply's `SendResult` as the tool answers it."""
    text = "\n".join(result.lines())
    if result.outcome not in (sending.OUTCOME_SENT, sending.OUTCOME_UNKNOWN):
        refusal = result.refusal or sending.Refusal(result.outcome, text)
        return refused(refusal.code, refusal.text or text, outcome=result.outcome)
    expires_at, marked = None, interrupt
    if result.message_id is not None:
        expires_at, marked = _marks(daemon, result.message_id, interrupt)
    return _result(text, {
        "outcome": result.outcome,
        "message_id": result.message_id,
        "recipients": [{"name": r.qualified, "delivery": r.delivery}
                       for r in result.recipients],
        "expires_at": expires_at,
        "interrupt": marked,
        "notes": list(result.notes),
    })


def _marks(daemon, message_id: str, interrupt: bool) -> tuple:
    """(expires_at, interrupt) as the posted message's routing line carries them."""
    try:
        msg = daemon.record.get_message(str(message_id))
    except Exception:  # noqa: BLE001 - the result says what it can
        return None, interrupt
    ml = machine_line.parse(msg.content) if msg is not None else None
    if ml is None:
        return None, interrupt
    return ml.expires_at, ml.interrupt


# --------------------------------------------------------------------------- the bodies

class Tools:
    """The six tools of one daemon. `add(server)` adds them to the SDK's server; each
    body runs on the daemon's tool pool, as the identity the bearer check placed the
    call as."""

    def __init__(self, daemon):
        self.daemon = daemon

    # ---- on the loop
    async def call(self, body, params: dict) -> CallToolResult:
        """Read who calls from the request's context, here on the loop where it is, and
        run `body(identity, claim, params)` on the tool pool."""
        access = get_access_token()
        if access is None or not access.client_id:
            return refused("credential", "comms couldn't tell which session this call "
                           "is from. Nothing was done.")
        claim = (access.claims or {}).get("claim")
        return await self.daemon.tool_calls.run(self._guarded, body, access.client_id,
                                                claim, params)

    @staticmethod
    def _guarded(body, identity, claim, params) -> CallToolResult:
        try:
            return body(identity, claim, params)
        except Exception as e:  # noqa: BLE001 - a tool answers; it never raises
            log.exception("comms' %s tool failed (%s)", getattr(body, "__name__", "?"),
                          type(e).__name__)
            return refused("failed", FAILED)

    # ---- on the pool
    def _context(self) -> sending.Context:
        ctx = inprocess.context(self.daemon, deadline=DEADLINE)
        ctx.cures = sending.CURES_TOOL
        return ctx

    def send(self, identity, claim, params) -> CallToolResult:
        sender = inprocess.bound_sender(self.daemon, claim, identity)
        if sender is None:
            return refused("reconnecting", RECONNECTING)
        to = params["to"]
        names = to.split(",") if isinstance(to, str) else [str(t) for t in to]
        tags = [t.strip() for t in names if t.strip()]
        reply_to = params.get("reply_to")
        args = _args(params, reply_to=None if reply_to is None else str(reply_to))
        result = sending.run(self._context(), args, tags=tags, sender=sender)
        return _send_result(self.daemon, result, bool(params.get("interrupt")))

    def reply(self, identity, claim, params) -> CallToolResult:
        sender = inprocess.bound_sender(self.daemon, claim, identity)
        if sender is None:
            return refused("reconnecting", RECONNECTING)
        args = _args(params, message_id=str(params["message_id"]))
        result = sending.run(self._context(), args, sender=sender)
        return _send_result(self.daemon, result, bool(params.get("interrupt")))

    def delete(self, identity, claim, params) -> CallToolResult:
        sender = inprocess.bound_sender(self.daemon, claim, identity)
        if sender is None:
            return refused("reconnecting", RECONNECTING_DELETE)
        result = sending.delete(self._context(), params["message_id"], sender=sender)
        fields = {"message_id": result.message_id, "withdrawn": list(result.withdrawn),
                  "delivered": list(result.delivered), "expired": list(result.expired)}
        if result.outcome != sending.DELETE_DELETED:
            refusal = result.refusal or sending.Refusal(result.outcome, "")
            text = "\n".join(result.lines()) or refusal.text
            return refused(refusal.code, text, **fields)
        return _result("\n".join(result.lines()), fields)

    def agents(self, identity, claim, params) -> CallToolResult:
        try:
            info = self._read("agents", {})
        except DaemonError as e:
            return _read_refused(e)
        rows, seen = [], set()
        for b in info.get("bindings") or ():
            seen.add(b["qualified"])
            rows.append({"name": b["qualified"], "running": True,
                         "harness": b.get("adapter"), "send_only": False})
        for s in info.get("standing") or ():
            if s.get("qualified") in seen:
                continue
            seen.add(s["qualified"])
            rows.append({"name": s["qualified"], "running": False, "harness": None,
                         "send_only": s.get("kind") == SEND_ONLY})
        text = render_agents(info, now=self.daemon.clock())
        return _result(text, {"agents": rows})

    def whoami(self, identity, claim, params) -> CallToolResult:
        name, scope = split_tag(identity)
        try:
            held = self._read("session", {"identity": identity})
        except DaemonError as e:
            return _read_refused(e)
        bound = bool(held.get("bound"))
        sid = held.get("session_id") if bound else None
        lines = [f"{identity} ({'bound' if bound else 'reconnecting'})"]
        if sid:
            lines.append(f"session {sid}")
        return _result("\n".join(lines), {
            "identity": identity, "name": name, "scope": scope or "global",
            "harness": held.get("adapter") if bound else None,
            "session_id": sid if isinstance(sid, str) and sid else None})

    def history(self, identity, claim, params) -> CallToolResult:
        limit = params.get("limit", HISTORY_DEFAULT)
        if not HISTORY_LIMIT[0] <= limit <= HISTORY_LIMIT[1]:
            return refused("limit-shape", f"limit {limit} is outside "
                           f"{HISTORY_LIMIT[0]}-{HISTORY_LIMIT[1]}. Nothing was read.")
        who = identity_of(params.get("identity") or identity)
        try:
            return _history(self.daemon, self._context(), who, limit,
                            self._read)
        except DaemonError as e:
            return _read_refused(e)

    def _read(self, method: str, params: dict) -> dict:
        client = inprocess.InProcessClient(self.daemon)
        return client.call(method, params)

    # ---- the server
    def add(self, server) -> None:
        """Add the six tools to `server`, the SDK's `MCPServer`, with this module's
        schemas and descriptions."""
        expiry = "24h"
        try:
            expiry = config.load().delivery.expiry
        except Exception:  # noqa: BLE001 - the description keeps the default
            log.warning("comms' [delivery] expiry couldn't be read for the send tool's "
                        "description; it says 24 hours")
        texts = descriptions(expiry)
        for name, fn in self._functions().items():
            server.add_tool(fn, name=name, description=texts[name],
                            structured_output=False)
            tool = server._tool_manager.get_tool(name)
            tool.parameters = INPUTS[name]
            tool.fn_metadata.output_schema = OUTPUTS[name]

    def _functions(self) -> dict:
        """The async parts, one per tool. Their signatures validate the arguments; the
        published schemas are `INPUTS`."""
        call = self.call

        async def send(to: list[str] | str, text: str, posture: Posture,
                       expires: str | int | None = None, interrupt: bool = False,
                       reply_to: str | int | None = None, new: bool = False,
                       new_name: bool = False) -> CallToolResult:
            return await call(self.send, {
                "to": to, "text": text, "posture": posture, "expires": expires,
                "interrupt": interrupt, "reply_to": reply_to, "new": new,
                "new_name": new_name})

        async def reply(message_id: str | int, text: str, posture: Posture | None = None,
                        expires: str | int | None = None,
                        interrupt: bool = False) -> CallToolResult:
            return await call(self.reply, {
                "message_id": message_id, "text": text, "posture": posture,
                "expires": expires, "interrupt": interrupt})

        async def agents() -> CallToolResult:
            return await call(self.agents, {})

        async def whoami() -> CallToolResult:
            return await call(self.whoami, {})

        async def history(identity: str | None = None,
                          limit: int = HISTORY_DEFAULT) -> CallToolResult:
            return await call(self.history, {"identity": identity, "limit": limit})

        async def delete(message_id: str | int) -> CallToolResult:
            return await call(self.delete, {"message_id": message_id})

        return {"send": send, "reply": reply, "agents": agents, "whoami": whoami,
                "history": history, "remove": delete}


def adder(daemon):
    """What adds comms' tools to the endpoint's server, for `daemon`."""
    return Tools(daemon).add


def _args(params: dict, *, reply_to=None, message_id=None) -> SimpleNamespace:
    """The verb's fields for a tool's send or reply, as `api.send` builds them: the
    tools pass none of the command line's other flags."""
    return SimpleNamespace(
        text=str(params["text"]), posture=params.get("posture"), cap=None, no_cap=False,
        audience=None, new=bool(params.get("new")), held=False, standing=False,
        new_name=bool(params.get("new_name")), interrupt=bool(params.get("interrupt")),
        expires=params.get("expires"), check=False, reply_to=reply_to,
        message_id=message_id)


def _read_refused(e: DaemonError) -> CallToolResult:
    from .. import wire

    if isinstance(e, DaemonOffline):
        return refused("offline", f"{e} Try again in a moment.")
    reason = (e.data or {}).get("reason") or str(e)
    return refused(wire.CODE_NAMES.get(e.code, "refused"), reason)


# --------------------------------------------------------------------------- history

#: What one message in `history` came to.
DELIVERED, WAITING, EXPIRED, WITHDRAWN = "delivered", "waiting", "expired", "withdrawn"


def _history(daemon, ctx: sending.Context, identity: str, limit: int, read):
    """`moxi comms history <identity> --limit <limit>`'s rows, in its words, with each
    message's state: withdrawn by its author, waiting for a recipient, expired before
    reaching one, or delivered.

    The verb's rendering lives in the CLI, which nothing in the daemon may import (it
    reads cards); its row is spelled again here, and a test holds the two lines equal."""
    window = None
    if is_auto_name(identity):
        window = read("binding_window", {"name": identity}).get("window")
        if window is None:
            text = (f"{identity}: no binding this daemon has seen. agent-N names are "
                    "never reused, so there is no history to show.")
            return _result(text, {"messages": []})
    with ctx.db() as db:
        room = directory.room_by_name(db, NAMES)
        msgs = ctx.record.history(room["room_id"], limit=limit) if room else []
        rows, effective = [], {}
        for m in msgs:
            ml = machine_line.parse(m.content)
            if ml is None:
                continue
            effective[m.id] = ml.audience or (effective.get(ml.reply_to)
                                              if ml.reply_to else None)
            author = attribution.from_record(db, m)
            if window is not None and not _in_window(m, window):
                continue
            addressed = any(tag_matches(t, identity) for t in ml.recipients)
            if author.qualified != identity and not addressed:
                continue
            rows.append((m, ml, author, "from" if author.qualified == identity else "to"))
        ids = [m.id for m, *_ in rows]
        dropped = _dropped(db, ids)
        waiting = _waiting(db, ids)
    lines = [] if rows else [f"no {NAMES} history for {identity}."]
    messages = []
    for m, ml, author, direction in rows:
        gone = dropped.get(str(m.id), {})
        lines.append(f"{m.id}  {m.timestamp}  {direction:<4} {author.qualified} "
                     f"({author.tier}, {attribution.rendered(author.attribution)})"
                     f"  -> {', '.join(ml.recipients)}"
                     + (f" re:{ml.reply_to}" if ml.reply_to else "")
                     + _audience(ml, effective) + _marks_text(ml) + _gone(m, gone))
        lines += [f"      {line}" for line in ml.text.splitlines()]
        messages.append({
            "id": str(m.id), "from": author.qualified, "to": list(ml.recipients),
            "text": ml.text, "at": str(m.timestamp),
            "attribution": attribution.rendered(author.attribution),
            "state": _state(m, gone, str(m.id) in waiting)})
    if window is not None:
        lines.append(f"(time-scoped to the {identity} binding: {window[0]} .. "
                     f"{window[1] if window[1] is not None else 'now'})")
    return _result("\n".join(lines), {"messages": messages})


def _in_window(msg, window) -> bool:
    from ..daemon import _epoch

    start, end = window
    ts = _epoch(msg.timestamp)
    return ts >= start - 1 and (end is None or ts <= end + 1)


def _dropped(db, ids) -> dict:
    """{message id: {name: why}} for the page's messages that left a queue undelivered."""
    try:
        return books.dropped_by_message(db, ids)
    except sqlite3.Error:
        return {}


def _waiting(db, ids) -> set:
    """The page's messages a recipient's queue still holds, waiting to be handed over."""
    mids = [int(i) for i in ids if str(i).isdigit()]
    if not mids:
        return set()
    marks = ", ".join("?" * len(mids))
    try:
        return {str(row[0]) for row in db.execute(
            f"SELECT DISTINCT message_id FROM queue WHERE state = ? "
            f"AND message_id IN ({marks})", [books.WAITING, *mids])}
    except sqlite3.Error:
        return set()


def _audience(ml, effective: dict) -> str:
    if ml.audience:
        return f" audience: {ml.audience}"
    inherited = effective.get(ml.reply_to) if ml.reply_to else None
    return f" audience: {inherited} (inherited)" if inherited else ""


def _marks_text(ml) -> str:
    if ml.open_mark:
        return f" {machine_line.OPEN_TOKEN}"
    if ml.posture != machine_line.POSTURE_OPEN:
        return f" {machine_line.POSTURE_TOKENS[ml.posture]}"
    return ""


def _gone(m, dropped: dict) -> str:
    bits = []
    if getattr(m, "withdrawn_at", None) or books.WITHDRAWN in dropped.values():
        bits.append("[withdrawn]")
    expired = [name for name, why in dropped.items() if why == books.EXPIRED]
    if expired:
        bits.append(f"[expired for {', '.join(expired)}]")
    reserved = [name for name, why in dropped.items() if why == books.RESERVED]
    if reserved:
        bits.append(f"[dropped for {', '.join(reserved)}: reserved]")
    return "".join(f" {bit}" for bit in bits)


def _state(m, gone: dict, waiting: bool) -> str:
    if getattr(m, "withdrawn_at", None) or books.WITHDRAWN in gone.values():
        return WITHDRAWN
    if waiting:
        return WAITING
    if books.EXPIRED in gone.values():
        return EXPIRED
    return DELIVERED


__all__ = ["DEADLINE", "INPUTS", "NAMES_OF_TOOLS", "OUTPUTS", "RECONNECTING", "Tools",
           "adder", "descriptions", "refused"]

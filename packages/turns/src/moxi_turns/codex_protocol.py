"""Pure helpers for the Codex app-server JSON-RPC protocol.

The app-server's Unix socket carries WebSockets, not JSON lines. A connection must
initialize before any other request and must start or resume a thread to receive that
thread's notifications. Delivery is steer-first because a turn can begin between an
idle reading and an input request.
"""

from __future__ import annotations

import re

JSONRPC = "2.0"
INVALID_REQUEST = -32600
#: JSON-RPC's code for a method the server does not have (an older Codex).
METHOD_NOT_FOUND = -32601
NO_ACTIVE_TURN_MESSAGE = "no active turn to steer"
_MISMATCH_RE = re.compile(r"expected active turn id `([^`]*)` but found `([^`]*)`")
ZERO_TURN_ID = "00000000-0000-0000-0000-000000000000"

STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "inProgress"
TERMINAL_STATUSES = ("completed", "interrupted", "failed")


class AppServerOffline(Exception):
    """The app-server socket is gone, closed, or did not answer in time."""


class NoReply(AppServerOffline):
    """A request went out and no answer came: the connection closed after the send, or
    the wait ran out. Codex may have acted on it, so a delivery it carried is not a
    refusal. Plain `AppServerOffline` means the request was never sent."""


class CodexError(Exception):
    """A JSON-RPC error returned by the app-server."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message
        self.data = data


class NoActiveTurn(CodexError):
    """The thread is idle, so delivery may fall back to ``turn/start``."""


class ActiveTurnMismatch(CodexError):
    """A steer named a stale turn; the refusal names the live one."""

    def __init__(self, code: int, message: str, expected: str, found: str, data=None):
        super().__init__(code, message, data)
        self.expected = expected
        self.found = found


def classify_error(error: dict) -> CodexError:
    code = error.get("code", 0)
    message = str(error.get("message", ""))
    data = error.get("data")
    if code == INVALID_REQUEST:
        match = _MISMATCH_RE.search(message)
        if match:
            return ActiveTurnMismatch(code, message, match.group(1), match.group(2), data)
        if NO_ACTIVE_TURN_MESSAGE in message:
            return NoActiveTurn(code, message, data)
    return CodexError(code, message, data)


def request(rid, method: str, params=None) -> dict:
    frame = {"jsonrpc": JSONRPC, "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def notification(method: str, params=None) -> dict:
    frame = {"jsonrpc": JSONRPC, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def text_input(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def initialize_params(client_name: str, version: str) -> dict:
    return {"clientInfo": {"name": client_name, "version": version},
            "capabilities": {"experimentalApi": True}}


def resume_params(thread_id: str, instructions: str | None = None,
                  options=None) -> dict:
    params = dict(options or {})
    params.update({"threadId": str(thread_id), "excludeTurns": True,
                   "approvalPolicy": "never"})
    if instructions:
        params["developerInstructions"] = instructions
    return params


def start_thread_params(cwd: str, instructions: str | None = None,
                        options=None) -> dict:
    params = dict(options or {})
    params.update({"cwd": cwd, "approvalPolicy": "never"})
    if instructions:
        params["developerInstructions"] = instructions
    return params


def steer_params(thread_id: str, expected_turn_id: str | None, text: str) -> dict:
    return {"threadId": str(thread_id),
            "expectedTurnId": str(expected_turn_id or ZERO_TURN_ID),
            "input": text_input(text)}


def start_params(thread_id: str, text: str) -> dict:
    return {"threadId": str(thread_id), "input": text_input(text)}


def method_missing(exc: BaseException) -> bool:
    """Whether `exc` is the app-server's answer that it has no such method."""
    return isinstance(exc, CodexError) and exc.code == METHOD_NOT_FOUND


def compact_params(thread_id: str) -> dict:
    return {"threadId": str(thread_id)}


def terminals_params(thread_id: str) -> dict:
    return {"threadId": str(thread_id)}


def terminate_params(thread_id: str, process_id: str) -> dict:
    return {"threadId": str(thread_id), "processId": process_id}


def terminal_ids(result) -> list[str]:
    """The process ids of a `thread/backgroundTerminals/list` answer, in its order. An
    answer of any other shape raises `ValueError`: a list turns cannot read is not an
    empty one."""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        raise ValueError("the background terminal list has no data list")
    ids = []
    for entry in data:
        process_id = entry.get("processId") if isinstance(entry, dict) else None
        if not isinstance(process_id, (str, int)) or isinstance(process_id, bool):
            raise ValueError("a background terminal has no process id")
        ids.append(str(process_id))
    return ids


#: A collab agent's status (`CollabAgentStatus`) as turns' state, and whether it is a
#: final notice.
COLLAB_STATES = {
    "pendingInit": ("pending", False), "running": ("running", False),
    "completed": ("done", True), "errored": ("failed", True),
    "interrupted": ("stopped", True), "shutdown": ("stopped", True),
    "notFound": ("unknown", False),
}
COLLAB_ITEM = "collabAgentToolCall"
#: A spawn as Codex's ``multi_agent_v2`` reports it: an item turns cannot read a
#: subagent's state from.
SUBAGENT_ACTIVITY_ITEM = "subAgentActivity"


def read_params(thread_id: str) -> dict:
    """`thread/read` with the thread's turns and their items."""
    return {"threadId": thread_id, "includeTurns": True}


def collab_state(status) -> tuple[str, bool | None]:
    """A collab agent status as `(state, final)`; one turns does not know reads
    `unknown`, with no notice to call final."""
    return COLLAB_STATES.get(status, ("unknown", None)) if isinstance(status, str) \
        else ("unknown", None)


def collab_notices(item) -> list[tuple[str, str | None, str | None]]:
    """A `collabAgentToolCall` item's notices, in order: `(agent thread id, status or
    None, sender thread id)` for each receiver and each agent its `agentsStates` names.
    Any other item gives none."""
    if not isinstance(item, dict) or item.get("type") != COLLAB_ITEM:
        return []
    sender = item.get("senderThreadId") if isinstance(item.get("senderThreadId"), str) \
        else None
    states = item.get("agentsStates") if isinstance(item.get("agentsStates"), dict) else {}
    ids = [r for r in item.get("receiverThreadIds") or () if isinstance(r, str) and r]
    ids += [k for k in states if isinstance(k, str) and k and k not in ids]
    out = []
    for agent_id in ids:
        state = states.get(agent_id)
        status = state.get("status") if isinstance(state, dict) else None
        out.append((agent_id, status if isinstance(status, str) else None, sender))
    return out


def thread_items(result) -> list:
    """The items of a `thread/read` answer, turn by turn in its order. Raises
    `ValueError` on any other shape: a history turns cannot read is not an empty one."""
    thread = result.get("thread") if isinstance(result, dict) else None
    turns = thread.get("turns") if isinstance(thread, dict) else None
    if not isinstance(turns, list):
        raise ValueError("the thread read has no turns list")
    items = []
    for turn in turns:
        turn_items = turn.get("items") if isinstance(turn, dict) else None
        if not isinstance(turn_items, list):
            raise ValueError("a turn of the thread read has no items list")
        items.extend(turn_items)
    return items


def thread_is_active(thread: dict | None) -> bool:
    return bool(thread and (thread.get("status") or {}).get("type") == "active")


def active_turn_id(turns_result: dict | None) -> str | None:
    for turn in (turns_result or {}).get("data") or []:
        if turn.get("status") == STATUS_IN_PROGRESS:
            return turn.get("id")
    return None


__all__ = [
    "JSONRPC", "INVALID_REQUEST", "METHOD_NOT_FOUND", "NO_ACTIVE_TURN_MESSAGE",
    "ZERO_TURN_ID",
    "STATUS_COMPLETED", "STATUS_IN_PROGRESS", "TERMINAL_STATUSES", "AppServerOffline",
    "NoReply", "CodexError", "NoActiveTurn", "ActiveTurnMismatch", "classify_error",
    "request", "notification",
    "text_input", "initialize_params", "resume_params", "start_thread_params",
    "steer_params", "start_params", "method_missing", "compact_params",
    "terminals_params", "terminate_params", "terminal_ids", "COLLAB_STATES", "COLLAB_ITEM",
    "SUBAGENT_ACTIVITY_ITEM",
    "read_params", "collab_state", "collab_notices", "thread_items", "thread_is_active",
    "active_turn_id",
]

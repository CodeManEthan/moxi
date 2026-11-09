"""The daemon's health, the announcement's `HEALTH`: whether the control socket answers
`status` within `ASK_WAIT` seconds.

The control socket is `moxi.ipc.address("connector", "control")`, newline JSON-RPC with
the core's `rpc.hello`. This reads it and nothing else: no network call.
"""

from __future__ import annotations

MODULE = "connector"
#: The control socket's endpoint name.
CONTROL = "control"
#: Seconds the daemon gets to answer.
ASK_WAIT = 2.0
#: The control socket's protocol versions this side speaks.
VERSIONS = (1, 1)
#: The fix of a daemon that doesn't answer.
RESTART = "moxi install connector"


def ask(method: str, params: object = None, *, wait: float = ASK_WAIT) -> object:
    """Call `method` on the running daemon's control socket. Raises `moxi.ipc.IpcError`
    (`NotServing` when nothing listens) when it can't be asked."""
    from moxi import ipc

    with ipc.connect(ipc.address(MODULE, CONTROL), timeout_s=wait) as client:
        client.hello(VERSIONS)
        return client.call(method, params, timeout_s=wait)


def health():
    """The daemon's `HEALTH` row."""
    from moxi import ipc
    from moxi.doctor import Row

    try:
        answer = ask("status")
    except ipc.IpcError as e:
        return Row(MODULE, "server", "fail",
                   f"the control socket doesn't answer within {ASK_WAIT:g} s: {e}", RESTART)
    url = answer.get("url") if isinstance(answer, dict) else None
    return Row(MODULE, "server", "ok",
               f"answers; the door's address is {url}" if url else
               "answers; the door has no public address yet")


__all__ = ["ASK_WAIT", "CONTROL", "MODULE", "VERSIONS", "ask", "health"]

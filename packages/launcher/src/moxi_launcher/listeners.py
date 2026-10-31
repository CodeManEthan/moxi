"""The launcher's registration with turns for session events (the `moxi.turns.listeners`
group), and the claim every listener runs first.

Standard library only at import, and imports nothing else of the launcher until a listener
is asked: turns' endpoint reads this module when it starts, and imports each function in
its own thread at its first ask.

Three rows. The ready recorder observes `ready` and `gone` (its answer is ignored). The
Codex seat listener and the handoff pointer answer `start`, the event whose answers turns
adds to start context; turns asks them only when the launcher is on its `start.order`, and
joins their answers in the order below.

**A launch belongs to the one session process the launcher started for it.** Each launch
stamps `MOXI_LAUNCHER_REQUEST` into its session's environment, and every process the session
starts inherits it, so the stamp only says which launch a session may belong to. The first
turns endpoint whose event matches what the launcher set on its process claims the launch
(`store.claim`), and from then on only that endpoint's events are recorded or answered. An
endpoint that carries the stamp without claiming it is a session started from inside the
launched one, an agent's own `claude -p` say, and every listener ignores it: no ids, no
state, no instructions, no pointer, no pick. Listeners run in the session's endpoint
process, so the endpoint is named by its own pid and start time.

An endpoint serves its session for the endpoint's own life, and a session may outlive it:
Claude starts a new endpoint for the same session when its plugins reload. The new
endpoint claims the launch again when its event names a session id the launch is known by
(its own, or one recorded for it, as after a `/clear`) and the endpoint that held it has
ended (`store.claim`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

LISTENERS = (
    (("ready", "gone"), "moxi_launcher.listen_ready:observe"),
    (("start",), "moxi_launcher.listen_codex:answer"),
    (("start",), "moxi_launcher.listen_handoff:answer"),
)

#: The launch's stamp: the request id of the launch that started the session.
REQUEST = "MOXI_LAUNCHER_REQUEST"
#: Set on a relaunch, whose handoff comes in its first turn.
RELAUNCH = "MOXI_LAUNCHER_RELAUNCH"
#: How long a listener waits for the launch log's write lock, in seconds. turns bounds
#: every listener of an event together (two seconds by default), and a listener holds the
#: lock for one short write.
STORE_TIMEOUT = 1.0

_me: tuple[int, str] | None = None
_me_lock = threading.Lock()


def endpoint_id() -> str:
    """This endpoint process, as the claim names it: its pid and start time.

    Read once per process and kept, so a later event in the same process names it the
    same way even when the system's boot time read moves.
    """
    global _me
    pid = os.getpid()
    with _me_lock:
        if _me is None or _me[0] != pid:
            from moxi import proc

            ident = proc.identity(pid)
            _me = (pid, f"{pid}@{ident.started:.6f}" if ident else str(pid))
        return _me[1]


def event_env(event) -> Mapping[str, str]:
    """The session's environment as the event carries it, else this process's."""
    env = getattr(event, "env", None)
    return env if env else dict(os.environ)


def open_store():
    """The launch log, opened for a listener; None when it was never made."""
    from moxi import store as core_store

    from . import store

    try:
        return store.connect(create=False, timeout=STORE_TIMEOUT)
    except core_store.StoreTooNew:
        raise
    except core_store.StoreError:
        return None


def claim(event, conn):
    """The launch row this event's endpoint holds, claiming it when it is free, or None.

    None when the session carries no launcher stamp, when the launch is unknown, and when
    the launch is another endpoint's (a nested session).
    """
    from . import store

    request_id = event_env(event).get(REQUEST)
    if not request_id or conn is None:
        return None
    return store.claim(conn, request_id, harness=event.harness,
                       session_id=getattr(event, "session_id", None),
                       endpoint=endpoint_id())


__all__ = ["LISTENERS", "REQUEST", "RELAUNCH", "STORE_TIMEOUT", "claim", "endpoint_id",
           "event_env", "open_store"]

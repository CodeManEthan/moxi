"""turns: the one interface to each harness's session (Moxi's `moxi_turns`).

Only turns puts input into a session (a turn, mail, `/clear`, `/exit`, and the context a
module adds at a session's start or at a prompt), and only turns takes session events
out of the harness. This package is turns' whole public API, the announcement's
`API = "moxi_turns"`; every submodule is internal.

    import moxi_turns as turns

    s = turns.find(session_id="…")   # or find(pid=…); turns.sessions() lists live ones
    s = turns.wait_ready(session_id="…", timeout=30)   # or pid=: found, and its Ready seen
    s.harness                        # "claude" | "codex" | "opencode"
    s.pid, s.endpoint_pid            # the harness process; the process serving the endpoint
    s.capabilities                   # frozenset of CAPABILITIES' names the session offers
    s.info; s.refresh()              # the endpoint's last `session` answer; ask again
    f = s.facts()    # Facts(session_id, turn_number, turn_open, turn, agents, draft, at,
                     #       stale, ready); turn: OpenTurn | None
    a = s.agents()   # Agents(entries, complete, at)

    d = s.deliver(text, *, only_if_idle=False, if_turn_number=None, refuse_draft=False,
                  refuse_agents=False, ref=None)
    d = s.steer(text, *, ref=None)
    d.id, d.ref              # id None until a hand-in whose answer never came is found
    d.wait_started(timeout)  # Started | Refused | Lost
    d.wait_ended(timeout)    # Ended | Refused | Lost

    r = s.command("clear" | "exit" | "compact", *, if_turn_number=None, timeout=None,
                  stop_shells=False)              # Ran(new_session_id, shells) | Refused | Failed
    w = s.watch()             # w.facts, then iterate Ready | TurnStarted | TurnSteered |
                              # TurnEnded, and one Gone; w.close()
    h = s.handle_stops(on_stop)   # one stop handler per session; h.close()
    s.close()                 # or `with turns.find(...) as s:`
    turns.check()             # rows for a doctor
    turns.claude_plugin_dirs()  # the Claude plugin folders, for a per-launch --plugin-dir
    s = turns.opencode.launch(cwd, *, instructions=None, env=None, options=None)
                              # an OpenCode host, experimental

A request the endpoint never got reads as refused (`Refused("gone")`); one it got and
did not answer in time has an unknown outcome, never a refusal (the `Delivery` above,
`Failed("unanswered")` for a command).

The wire under it is turns' socket protocol, `contracts/socket-protocol.md`.

The package imports nothing when it loads: each name above loads its submodule on first
use (PEP 562). A hook process imports `moxi_turns.hooks`, which pays this package first,
and must not pay the core, sockets or threads with it (the hooks' law L3).
"""

from __future__ import annotations

#: Each public name and the submodule that holds it.
_WHERE = {
    "codex": "codex",
    **dict.fromkeys(("find", "sessions", "wait_ready", "Session", "Delivery", "Watch",
                     "StopHandler", "TurnsError", "VersionError"), "client"),
    **dict.fromkeys(("check", "Row"), "doctor"),
    "claude_plugin_dirs": "claude_setup",
    "opencode": "opencode",     # a host's namespace (`turns.opencode.launch`): the module
    **dict.fromkeys(("Facts", "Agents", "Agent", "OpenTurn", "Started", "Refused", "Lost",
                     "Ended", "Ran", "Shells", "LeftShell", "Failed", "Ready",
                     "TurnStarted", "TurnSteered", "TurnEnded", "DeliveryRef", "Gone",
                     "StopFacts", "SessionEvent", "CAPABILITIES", "OUTCOMES",
                     "DELIVERY_REFUSALS", "COMMAND_REFUSALS", "READY_SOURCES", "COMMANDS"),
                    "results"),
}

__all__ = [
    "find", "sessions", "wait_ready", "check", "codex", "claude_plugin_dirs", "opencode",
    "Session", "Delivery", "Watch", "StopHandler", "Row", "TurnsError", "VersionError",
    "Facts", "Agents", "Agent", "OpenTurn", "Started", "Refused", "Lost", "Ended", "Ran",
    "Shells", "LeftShell", "Failed", "Ready", "TurnStarted", "TurnSteered", "TurnEnded",
    "DeliveryRef", "Gone", "StopFacts", "SessionEvent",
    "CAPABILITIES", "OUTCOMES", "DELIVERY_REFUSALS", "COMMAND_REFUSALS", "READY_SOURCES",
    "COMMANDS",
]


def __getattr__(name: str):
    if name == "codex":
        value = __import__(f"{__name__}.codex", fromlist=[name])
        globals()[name] = value
        return value
    where = _WHERE.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = __import__(f"{__name__}.{where}", fromlist=[name])
    # A name that is its submodule's own name is a host's namespace: the module itself.
    value = module if where == name else getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})

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
    e = s.environ(names)   # {name: value | None}: the session's own environment, read
                           # from its endpoint (capability environ; at most 64 names)

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
    adds = turns.launch_additions(harness, *, cwd, env, session_id=None, commands=())
                              # LaunchAdditions(harness, argv, env, options, notes): what a
                              # launch about to start adds; modules contribute through the
                              # group moxi.turns.additions (a Contribution each;
                              # its mcp_servers McpServer(url, token_env) values)
    p = turns.start_plan(harness, env=None)   # StartPlan(harness, total, limit, ceiling,
                              # entries, skipped): the start list as a session would get
                              # it, each StartEntry(name, kind, share, size) with its
                              # nominal share; runs no listener
    s = turns.codex.launch(cwd, *, instructions=None, env=None, window=True, options=None,
                           verify=None, additions=None, resume=None)   # a Codex host
    kw = turns.codex.launch_args(argv)   # moxi-turns-codex-host's flags as launch()'s
                                         # keywords; that entry runs the host as a
                                         # process of its own: wait_ready(pid=its pid)
    turns.codex_host_available()         # True when the Codex host can start (the
                                         # `codex` extra is installed); imports nothing
    lim = turns.codex_rate_limits(timeout=3.0)   # CodexLimits(payload, received_at, via)
                              # | None: a live Codex host's `usage.codex_limits` (via
                              # "host"), else an app-server of its own ("app-server")
    s.mcp_reload()            # a Codex host (capability mcp-token): settle its launch's
                              # MCP servers, reloading a failed one; {reloaded, servers}
    s = turns.opencode.launch(cwd, *, instructions=None, env=None, options=None,
                              additions=None)   # an OpenCode host, experimental
    turns.Guard(name, calls, *, who="any", match=None, match_flags="", judge=None,
                deny="", wants=(), on_error="allow", deadline_ms=500, switch=None,
                doc="")       # a guard, declared in a module's GUARDS (group
                              # moxi.turns.guards); its judge takes a ToolCall (prompts:
                              # Prompt values) and answers None | Allow(line) | Deny(reason)

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
    **dict.fromkeys(("launch_additions", "LaunchAdditions", "Contribution",
                     "AdditionsRequest", "McpServer"), "additions"),
    "codex_host_available": "codex_host",
    **dict.fromkeys(("codex_rate_limits", "CodexLimits"), "codex_appserver"),
    **dict.fromkeys(("start_plan", "StartPlan", "StartEntry"), "listeners"),
    "opencode": "opencode",     # a host's namespace (`turns.opencode.launch`): the module
    **dict.fromkeys(("Facts", "Agents", "Agent", "OpenTurn", "Started", "Refused", "Lost",
                     "Ended", "Ran", "Shells", "LeftShell", "Failed", "Ready",
                     "TurnStarted", "TurnSteered", "TurnEnded", "DeliveryRef", "Gone",
                     "StopFacts", "SessionEvent", "CAPABILITIES", "OUTCOMES",
                     "DELIVERY_REFUSALS", "COMMAND_REFUSALS", "READY_SOURCES", "COMMANDS"),
                    "results"),
    **dict.fromkeys(("Guard", "ToolCall", "Prompt", "Allow", "Deny"), "guards"),
}

__all__ = [
    "find", "sessions", "wait_ready", "codex", "codex_host_available", "opencode",
    "codex_rate_limits", "CodexLimits", "start_plan", "StartPlan", "StartEntry",
    "launch_additions", "LaunchAdditions", "Contribution", "AdditionsRequest",
    "McpServer", "Session", "Delivery", "Watch", "StopHandler", "TurnsError", "VersionError",
    "Facts", "Agents", "Agent", "OpenTurn", "Started", "Refused", "Lost", "Ended", "Ran",
    "Shells", "LeftShell", "Failed", "Ready", "TurnStarted", "TurnSteered", "TurnEnded",
    "DeliveryRef", "Gone", "StopFacts", "SessionEvent",
    "CAPABILITIES", "OUTCOMES", "DELIVERY_REFUSALS", "COMMAND_REFUSALS", "READY_SOURCES",
    "COMMANDS", "Guard", "ToolCall", "Prompt", "Allow", "Deny",
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

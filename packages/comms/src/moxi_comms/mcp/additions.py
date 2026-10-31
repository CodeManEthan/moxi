"""comms' contribution to a launch (turns' `moxi.turns.additions` group): its MCP server.

Standard library only, and imports nothing of comms or of the endpoint's libraries:
turns imports it at every launch and in the doctor, and asks it before the session
exists.

**One fact decides whether a launch carries comms' entry: the pair of variables the
wrapper stamps, `MOXI_COMMS_MCP_TOKEN` and `MOXI_COMMS_MCP_URL`, both non-empty.** The
wrapper that launches a session with an identity asks the daemon whether it serves
comms' tools; only when it does, the wrapper mints the session's token and stamps it
with the URL the daemon answered. Everything that depends on the entry reads that pair:
this contributor, the start text (`moxi_comms.listener.tools_line`), and the Codex
launch's network default. The contributor never reads comms' `[mcp]` settings, since
the daemon read them at its start and its answer is what the wrapper stamped.

The contribution is the server, `comms`, at exactly the stamped URL, with its token
taken from `MOXI_COMMS_MCP_TOKEN`, and Claude's rules for its six tools. turns renders
the server per harness and names the variable, never its value; this contributor checks
only that the value is there, and never returns it. A launch that comms' wrapper didn't
start carries no token, and gets no entry.
"""

from __future__ import annotations

#: The session's token for comms' tools, minted per launch by the wrapper.
TOKEN_ENV = "MOXI_COMMS_MCP_TOKEN"
#: The address of comms' tools for this session, as the daemon answered it.
URL_ENV = "MOXI_COMMS_MCP_URL"
#: `off` when the session's shell can't reach comms (a Codex sandbox with its network
#: off), so its tools are its one way to send.
SHELL_REACH_ENV = "MOXI_COMMS_SHELL_REACH"
#: What `MOXI_COMMS_SHELL_REACH` holds when the shell can't reach comms.
SHELL_REACH_OFF = "off"
#: The variables a wrapper stamps for comms' tools, and scrubs from what it inherits.
STAMPS = (TOKEN_ENV, URL_ENV, SHELL_REACH_ENV)

#: The server's name in a launch, which harnesses put in each tool's name.
SERVER = "comms"
#: The six tools, as the endpoint names them.
TOOLS = ("send", "reply", "agents", "whoami", "history", "delete")
#: Claude's permission rule for each tool.
CLAUDE_RULES = tuple(f"mcp__{SERVER}__{tool}" for tool in TOOLS)


def carries(env) -> bool:
    """Whether a launch's environment carries comms' entry: the token and the URL, both
    non-empty. Reads the token only to see that it is there."""
    return bool(env.get(TOKEN_ENV)) and bool(env.get(URL_ENV))


def additions(request):
    """turns asks this at a launch: comms' MCP server when `request.env` carries the
    stamped pair, else None."""
    env = request.env
    if not carries(env):
        return None
    from moxi_turns import Contribution, McpServer

    return Contribution(mcp_servers={SERVER: McpServer(url=env[URL_ENV], token_env=TOKEN_ENV)},
                        tool_permissions={"claude": CLAUDE_RULES})


__all__ = ["CLAUDE_RULES", "SERVER", "SHELL_REACH_ENV", "SHELL_REACH_OFF", "STAMPS",
           "TOKEN_ENV", "TOOLS", "URL_ENV", "additions", "carries"]

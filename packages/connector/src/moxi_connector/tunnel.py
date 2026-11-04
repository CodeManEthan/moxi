"""The connector's tunnel target, `connector/mcp`, read by tunnel from `moxi.tunnel`.

It is the connector's one public door: the MCP endpoint and, on the same origin, its
sign-in. tunnel forwards the address to the loopback port the `port` setting names.
"""

MCP = {"reach": "public", "port_setting": "port",
       "doc": "the public MCP door for the Claude apps"}

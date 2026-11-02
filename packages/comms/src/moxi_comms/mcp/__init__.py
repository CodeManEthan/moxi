"""comms' tools for a session's model, served by the daemon over MCP.

`tokens` holds the per-session token: minting it, the daemon's registry of its hash, and
when it is revoked. Importing this package imports nothing else, so a launch path that
only mints a token never loads the endpoint's libraries.
"""
